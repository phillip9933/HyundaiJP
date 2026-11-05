"""Japan vehicle polling, commands, camera state and diagnostics."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import datetime as dt
from datetime import timedelta
from functools import partial
import logging
from typing import Any, Literal, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ._api import (
    ClimateRequestOptions,
    POIInfo,
    ScheduleChargingClimateRequestOptions,
    SVMDetails,
    Token,
    Vehicle,
    VehicleManager,
    WindowRequestOptions,
)
from ._api.const import ORDER_STATUS, WINDOW_STATE
from ._api.exceptions import (
    APIError,
    AuthenticationError,
    RateLimitingError,
    RequestTimeoutError,
    UnsupportedControlError,
)
from ._api.svm_image import render_views
from .const import (
    CONF_ENABLE_GEOLOCATION_ENTITY,
    CONF_FORCE_REFRESH_INTERVAL,
    CONF_NO_FORCE_REFRESH_HOUR_FINISH,
    CONF_NO_FORCE_REFRESH_HOUR_START,
    CONF_TOKEN,
    CONF_USE_EMAIL_WITH_GEOCODE_API,
    DEFAULT_ENABLE_GEOLOCATION_ENTITY,
    DEFAULT_FORCE_REFRESH_INTERVAL,
    DEFAULT_NO_FORCE_REFRESH_HOUR_FINISH,
    DEFAULT_NO_FORCE_REFRESH_HOUR_START,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_EMAIL_WITH_GEOCODE_API,
    DOMAIN,
    HYUNDAI_BRAND,
    JAPAN_REGION,
    OffPeakChargingMode,
)

_LOGGER = logging.getLogger(__name__)

# Sent by set_svm_dewarp and consumed by the SVM image entities.
SIGNAL_SVM_RENDER = DOMAIN + "_{}_svm_render"
PollMode = Literal["check", "cached", "force"]
_T = TypeVar("_T")


class _ConfirmedCommandRefreshError(HomeAssistantError):
    """A successful service result could not be followed by updated telemetry."""


class _UnconfirmedCommandError(HomeAssistantError):
    """A submitted command has a known, unsuccessful confirmation outcome."""


class JapanDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage Japan commands, polling, camera state and completion tracking."""

    _POST_COMMAND_WATCH_SECONDS = 120
    _POST_COMMAND_POLL_INTERVAL_SECONDS = 2

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize."""
        self._action_lock = asyncio.Lock()
        self._shutdown_started = False
        self._api_jobs: set[asyncio.Future[Any]] = set()
        self._svm_details: dict[str, SVMDetails] = {}
        # Per-vehicle SVM fisheye dewarp toggle (local UI state, off by default).
        self._svm_dewarp: dict[str, bool] = {}
        # Rendered SVM views cache: vehicle_id -> ((captured_at, dewarp), views).
        # Invalidated by key change on a new capture or a switch toggle — no
        # explicit invalidation hooks needed.
        self._svm_views: dict[
            str, tuple[tuple[dt.datetime | None, bool], dict[str, bytes]]
        ] = {}
        # Vehicles whose last poll failed with a vehicle-scoped API error.
        self._failed_vehicles: set[str] = set()
        # Consecutive failed polls per vehicle (drives the "skip it" hint).
        self._vehicle_failures: dict[str, int] = {}

        self.vehicle_manager = VehicleManager(
            region=JAPAN_REGION,
            brand=HYUNDAI_BRAND,
            username=config_entry.data[CONF_USERNAME],
            password=config_entry.data[CONF_PASSWORD],
            pin=config_entry.data[CONF_PIN],
            geocode_api_enable=config_entry.options.get(
                CONF_ENABLE_GEOLOCATION_ENTITY, DEFAULT_ENABLE_GEOLOCATION_ENTITY
            ),
            geocode_api_use_email=config_entry.options.get(
                CONF_USE_EMAIL_WITH_GEOCODE_API, DEFAULT_USE_EMAIL_WITH_GEOCODE_API
            ),
            language=hass.config.language,
            token=Token.from_dict(config_entry.data.get(CONF_TOKEN, None))
            if config_entry.data.get(CONF_TOKEN, None)
            else None,
        )
        self.scan_interval: int = (
            config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL) * 60
        )
        self.force_refresh_interval: int = (
            config_entry.options.get(
                CONF_FORCE_REFRESH_INTERVAL, DEFAULT_FORCE_REFRESH_INTERVAL
            )
            * 60
        )
        self.no_force_refresh_hour_start: int = config_entry.options.get(
            CONF_NO_FORCE_REFRESH_HOUR_START, DEFAULT_NO_FORCE_REFRESH_HOUR_START
        )
        self.no_force_refresh_hour_finish: int = config_entry.options.get(
            CONF_NO_FORCE_REFRESH_HOUR_FINISH, DEFAULT_NO_FORCE_REFRESH_HOUR_FINISH
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=min(self.scan_interval, self.force_refresh_interval)
            ),
        )
        _LOGGER.debug(
            "%s - Polling configured: scan_interval=%ds, "
            "force_refresh_interval=%ds, update_interval=%ds, "
            "no_force_refresh_hours=%d-%d",
            DOMAIN,
            self.scan_interval,
            self.force_refresh_interval,
            min(self.scan_interval, self.force_refresh_interval),
            self.no_force_refresh_hour_start,
            self.no_force_refresh_hour_finish,
        )
        self._closing = False
        self._svm_received_at: dict[str, dt.datetime] = {}
        self._svm_render_locks: dict[str, asyncio.Lock] = {}
        self._svm_render_diagnostics: dict[str, dict[str, Any]] = {}
        self._post_command_watch_tasks: dict[str, asyncio.Task[None]] = {}
        self._post_command_watch_deadlines: dict[str, float] = {}
        self._post_command_watch_diagnostics: dict[str, dict[str, Any]] = {}

    async def _async_api_job(self, target: Callable[..., _T], *args: Any) -> _T:
        """Keep executor work owned until completion, even if its caller cancels."""
        if getattr(self, "_shutdown_started", False):
            raise HomeAssistantError("The integration is shutting down")
        if not hasattr(self, "_api_jobs"):
            self._api_jobs = set()
        job = asyncio.ensure_future(self.hass.async_add_executor_job(target, *args))
        self._api_jobs.add(job)
        job.add_done_callback(self._api_job_finished)
        return await asyncio.shield(job)

    @callback
    def _api_job_finished(self, job: asyncio.Future[Any]) -> None:
        self._api_jobs.discard(job)
        if not job.cancelled():
            job.exception()

    async def _async_close_manager(self) -> None:
        async with self._action_lock:
            await asyncio.gather(
                *getattr(self, "_api_jobs", ()), return_exceptions=True
            )
            await self.hass.async_add_executor_job(self.vehicle_manager.close)

    async def _async_stop_polling_and_close_manager(self) -> None:
        """Stop polling, drain running API work and release the manager."""
        self._shutdown_started = True
        try:
            await super().async_shutdown()
        finally:
            shutdown = asyncio.create_task(self._async_close_manager())
            cancelled = False
            while True:
                try:
                    await asyncio.shield(shutdown)
                    break
                except asyncio.CancelledError:
                    if shutdown.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError

    async def _async_poll_data(self) -> dict[str, Any]:
        """Update data via library. Called by update_coordinator periodically.

        Allow to update for the first time without further checking
        Allow force update, if time diff between latest update and `now` is greater than force refresh delta
        """
        _LOGGER.debug(
            "%s - _async_update_data called, scan_interval=%ds, force_refresh_interval=%ds",
            DOMAIN,
            self.scan_interval,
            self.force_refresh_interval,
        )
        try:
            await self.async_check_and_refresh_token()
        except AuthenticationError as AuthError:
            raise ConfigEntryAuthFailed(AuthError) from AuthError
        except Exception as err:
            # Transient API errors must be surfaced as UpdateFailed rather than
            # propagating as unexpected exceptions.  HA's update coordinator
            # counts unexpected exceptions and cancels the config entry after
            # enough consecutive failures, which makes all entities permanently
            # unavailable until the integration is manually reloaded.
            # Raising UpdateFailed(retry_after=60) keeps entities temporarily
            # unavailable and schedules an automatic retry after 60 seconds
            # instead of waiting for the next full poll interval.
            # See: https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/1538
            raise UpdateFailed(
                f"Token refresh failed, will retry in 60s: {err}",
                retry_after=60,
            ) from err
        current_hour = dt_util.now().hour

        in_force_window = (
            (self.no_force_refresh_hour_start <= self.no_force_refresh_hour_finish)
            and (
                current_hour < self.no_force_refresh_hour_start
                or current_hour >= self.no_force_refresh_hour_finish
            )
        ) or (
            (self.no_force_refresh_hour_start >= self.no_force_refresh_hour_finish)
            and (
                current_hour < self.no_force_refresh_hour_start
                and current_hour >= self.no_force_refresh_hour_finish
            )
        )

        await self._async_poll_vehicles(mode="check" if in_force_window else "cached")
        return self.data

    async def _async_update_all_unlocked(self) -> None:
        """Update vehicle data."""
        await self.async_check_and_refresh_token()
        await self._async_poll_vehicles(mode="cached")
        self.async_set_updated_data(self.data)

    async def _async_force_update_all_unlocked(self) -> None:
        """Force refresh vehicle data and update it."""
        await self.async_check_and_refresh_token()
        await self._async_poll_vehicles(mode="force")
        self.async_set_updated_data(self.data)

    async def _async_poll_vehicles(self, *, mode: PollMode) -> None:
        """Poll each vehicle on its own so one failing vehicle can't fail all.

        APIError is vehicle-scoped. Rate limiting and unknown exceptions are
        account-scoped and raise UpdateFailed so Home Assistant backs off.
        """
        failed: set[str] = set()
        auth_failures = 0
        for vehicle_id in list(self.vehicle_manager.vehicles):
            try:
                await self._async_poll_vehicle(vehicle_id, mode=mode)
            except AuthenticationError as err:
                # The token is only repaired (refresh or re-login) by the next
                # cycle's token check, which raises ConfigEntryAuthFailed itself
                # if the credentials are bad.
                auth_failures += 1
                failed.add(vehicle_id)
                self._log_vehicle_failure(vehicle_id, err)
                if auth_failures == len(self.vehicle_manager.vehicles):
                    raise UpdateFailed(
                        f"Authentication failed for every vehicle: {err}"
                    ) from err
            except RateLimitingError as err:
                raise UpdateFailed(
                    f"Rate limited by the API: {err}", retry_after=2 * 60 * 60
                ) from err
            except APIError as err:
                failed.add(vehicle_id)
                self._log_vehicle_failure(vehicle_id, err)
            except Exception as err:
                # UpdateFailed is logged without a traceback.
                _LOGGER.exception("%s - Error communicating with API", DOMAIN)
                raise UpdateFailed(f"Error communicating with API: {err}") from err
            else:
                self._vehicle_failures.pop(vehicle_id, None)
        self._failed_vehicles = failed

    async def _async_poll_vehicle(self, vehicle_id: str, *, mode: PollMode) -> None:
        """Update one vehicle, falling back to the cached state if a force fails."""
        try:
            if mode == "check":
                await self._async_api_job(
                    self.vehicle_manager.check_and_force_update_vehicle,
                    self.force_refresh_interval,
                    vehicle_id,
                )
            elif mode == "force":
                await self._async_api_job(
                    self.vehicle_manager.force_refresh_vehicle_state, vehicle_id
                )
            else:
                await self._async_api_job(
                    self.vehicle_manager.update_vehicle_with_cached_state, vehicle_id
                )
        except Exception as err:
            if mode == "cached":
                raise
            _LOGGER.warning(
                "%s - Force refresh failed for %s (%s), using cached state: %s",
                DOMAIN,
                self.vehicle_manager.vehicles[vehicle_id].name,
                vehicle_id,
                err,
            )
            await self._async_api_job(
                self.vehicle_manager.update_vehicle_with_cached_state, vehicle_id
            )

    def _log_vehicle_failure(self, vehicle_id: str, err: Exception) -> None:
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        failures = self._vehicle_failures.get(vehicle_id, 0) + 1
        self._vehicle_failures[vehicle_id] = failures
        _LOGGER.warning(
            "%s - Failed to update %s (%s): %s: %s",
            DOMAIN,
            vehicle.name,
            vehicle_id,
            type(err).__name__,
            err,
        )
        if failures == 3:
            # A vehicle the backend no longer serves (e.g. a sold car) fails
            # every poll; stop polling it by disabling the device in HA.
            _LOGGER.warning(
                "%s - Vehicle %s (%s) failed %s polls in a row. If the backend "
                "no longer serves it, disable its device in Home Assistant "
                "to stop polling it.",
                DOMAIN,
                vehicle.name,
                vehicle_id,
                failures,
            )

    def vehicle_available(self, vehicle_id: str) -> bool:
        """Return False if the vehicle's last poll failed."""
        return vehicle_id not in self._failed_vehicles

    async def async_supports_svm(self, vehicle_id: str) -> bool:
        """Return whether the given vehicle supports SVM.

        Read the profile capability cached on the vehicle; no request is needed.
        """
        vehicle = self.vehicle_manager.vehicles.get(vehicle_id)
        if vehicle is None:
            return False
        return bool(vehicle.supports.svm)

    def svm_dewarp_enabled(self, vehicle_id: str) -> bool:
        """Return the per-vehicle SVM fisheye dewarp preference."""
        return self._svm_dewarp.get(vehicle_id, False)

    def set_svm_dewarp(self, vehicle_id: str, enabled: bool) -> None:
        """Set the per-vehicle SVM fisheye dewarp preference."""
        self._svm_dewarp[vehicle_id] = enabled
        # The render key changes with the toggle, but captured_at does not —
        # without this signal the image proxy keeps serving the pre-toggle
        # image until the next capture.
        async_dispatcher_send(self.hass, SIGNAL_SVM_RENDER.format(vehicle_id))

    def get_cached_svm_details(self, vehicle_id: str) -> SVMDetails | None:
        """Return cached SVM details for a vehicle, or None if not yet fetched."""
        return self._svm_details.get(vehicle_id)

    @callback
    def _sync_vehicle_enabled_states(self) -> None:
        """Stop polling vehicles whose device the user disabled in HA."""
        registry = dr.async_get(self.hass)
        for vehicle_id, vehicle in self.vehicle_manager.vehicles.items():
            identifiers = {(DOMAIN, vehicle_id)}
            if vehicle.VIN:
                identifiers.add((DOMAIN, f"vin:{vehicle.VIN}"))
            device = registry.async_get_device(identifiers=identifiers)
            if device is not None and device.disabled:
                self.vehicle_manager.disable_vehicle(vehicle_id)
            else:
                self.vehicle_manager.enable_vehicle(vehicle_id)

    async def async_start_climate_default(self, vehicle_id: str) -> None:
        """Start climate with default options (API fills sensible defaults)."""
        await self.async_start_climate(vehicle_id, ClimateRequestOptions())

    def _build_schedule_options_from_vehicle(
        self, vehicle: Vehicle
    ) -> ScheduleChargingClimateRequestOptions:
        """Build schedule options from current vehicle state for partial updates."""
        return ScheduleChargingClimateRequestOptions(
            first_departure=ScheduleChargingClimateRequestOptions.DepartureOptions(
                enabled=vehicle.ev_first_departure_enabled or False,
                days=vehicle.ev_first_departure_days or [0],
                time=vehicle.ev_first_departure_time or dt.time(),
            ),
            second_departure=ScheduleChargingClimateRequestOptions.DepartureOptions(
                enabled=vehicle.ev_second_departure_enabled or False,
                days=vehicle.ev_second_departure_days or [0],
                time=vehicle.ev_second_departure_time or dt.time(),
            ),
            charging_enabled=vehicle.ev_schedule_charge_enabled or False,
            off_peak_start_time=vehicle.ev_off_peak_start_time or dt.time(),
            off_peak_end_time=vehicle.ev_off_peak_end_time or dt.time(),
            off_peak_charge_only_enabled=vehicle.ev_off_peak_charge_only_enabled
            or False,
            climate_enabled=vehicle.ev_first_departure_climate_enabled or False,
            temperature=vehicle.ev_first_departure_climate_temperature or 21.0,
            defrost=vehicle.ev_first_departure_climate_defrost or False,
        )

    async def async_set_schedule_charge_enabled(
        self, vehicle_id: str, enabled: bool
    ) -> None:
        """Toggle scheduled charging on/off."""
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        options = self._build_schedule_options_from_vehicle(vehicle)
        options.charging_enabled = enabled
        await self.async_schedule_charging_and_climate(vehicle_id, options)

    async def async_set_off_peak_charge_only_enabled(
        self, vehicle_id: str, enabled: bool
    ) -> None:
        """Toggle off-peak charge only on/off."""
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        options = self._build_schedule_options_from_vehicle(vehicle)
        options.off_peak_charge_only_enabled = enabled
        await self.async_schedule_charging_and_climate(vehicle_id, options)

    async def async_set_off_peak_charging(
        self,
        vehicle_id: str,
        *,
        mode: OffPeakChargingMode | None = None,
        start: dt.time | None = None,
        end: dt.time | None = None,
    ) -> None:
        """Set the off-peak charging schedule mode and/or window.

        ``mode`` maps to the (charging_enabled, off_peak_charge_only_enabled)
        pair the API expects: ``OFF`` disables scheduled charging, ``TIME``
        charges only during the off-peak window (time priority), ``TARGET``
        prefers off-peak tariffs but continues past the window to reach the
        target SoC (target priority). ``mode=None`` preserves the current mode
        and only adjusts the window — used by the time entities. Other
        schedule fields (departures) are preserved.
        """
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        options = self._build_schedule_options_from_vehicle(vehicle)
        if mode is not None:
            if mode is OffPeakChargingMode.OFF:
                options.charging_enabled = False
            elif mode is OffPeakChargingMode.TIME:
                options.charging_enabled = True
                options.off_peak_charge_only_enabled = True
            elif mode is OffPeakChargingMode.TARGET:
                options.charging_enabled = True
                options.off_peak_charge_only_enabled = False
        if start is not None:
            options.off_peak_start_time = start
        if end is not None:
            options.off_peak_end_time = end
        await self.async_schedule_charging_and_climate(vehicle_id, options)

    async def async_set_departure_enabled(
        self, vehicle_id: str, departure_num: int, enabled: bool
    ) -> None:
        """Toggle a departure schedule on/off."""
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        options = self._build_schedule_options_from_vehicle(vehicle)
        departure = (
            options.first_departure if departure_num == 1 else options.second_departure
        )
        if departure is not None:
            departure.enabled = enabled
        # reservFlag (charging_enabled) must be 1 for departure slots to take
        # effect. If the vehicle doesn't expose ev_schedule_charge_enabled
        # (None), the builder defaults it to False, causing the API to accept
        # the request but ignore per-slot reservChargeSet.
        if enabled and not options.charging_enabled:
            options.charging_enabled = True
        await self.async_schedule_charging_and_climate(vehicle_id, options)

    async def async_set_departure_climate_enabled(
        self, vehicle_id: str, departure_num: int, enabled: bool
    ) -> None:
        """Toggle departure climate on/off."""
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        options = self._build_schedule_options_from_vehicle(vehicle)
        options.climate_enabled = enabled
        await self.async_schedule_charging_and_climate(vehicle_id, options)

    async def async_set_departure_defrost(
        self, vehicle_id: str, departure_num: int, enabled: bool
    ) -> None:
        """Toggle departure defrost on/off."""
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        options = self._build_schedule_options_from_vehicle(vehicle)
        options.defrost = enabled
        await self.async_schedule_charging_and_climate(vehicle_id, options)

    async def _async_save_token(self) -> None:
        """Persist the latest token into the config entry."""
        config_entry = self.config_entry
        assert config_entry is not None
        token = self.vehicle_manager.token
        if token is None:
            return
        new_token = token.to_dict()
        # Only update if token actually changed
        if new_token and new_token != config_entry.data.get(CONF_TOKEN):
            updated_data = {**config_entry.data, CONF_TOKEN: new_token}
            self.hass.config_entries.async_update_entry(config_entry, data=updated_data)

    def supports_command(self, command: str) -> bool:
        """Whether this API implements the named vehicle command."""
        commands = self.vehicle_manager.supported_commands
        return commands is None or command in commands

    def _require_command(self, command: str) -> None:
        self._require_remote_commands()
        if not self.supports_command(command):
            raise HomeAssistantError(
                "This region does not support this vehicle command"
            )

    def _ensure_not_closing(self) -> None:
        if getattr(self, "_closing", False):
            raise HomeAssistantError("The integration is shutting down")

    def _require_remote_commands(self) -> None:
        self._ensure_not_closing()

    def _require_force_refresh(self) -> None:
        self._ensure_not_closing()

    @asynccontextmanager
    async def _async_region_operation(self) -> AsyncIterator[None]:
        self._ensure_not_closing()
        if self._action_lock.locked():
            raise HomeAssistantError("Another vehicle operation is in progress")
        async with self._action_lock:
            yield

    async def _async_complete_operation(self, operation: Awaitable[_T]) -> _T:
        """Finish Japan work before propagating cancellation to its lock owner."""
        task = asyncio.ensure_future(operation)
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
            else:
                if cancelled:
                    raise asyncio.CancelledError
                return result

    async def _async_run_executor_job(
        self, target: Callable[..., _T], *args: Any
    ) -> _T:
        """Drain non-cancellable executor work before releasing Japan's lock."""
        return await self._async_complete_operation(self._async_api_job(target, *args))

    async def async_shutdown(self) -> None:
        """Drain active Japan operations before disposing of their API sessions."""
        self._closing = True
        await self._async_complete_operation(self._async_shutdown_resources())

    async def _async_shutdown_resources(self) -> None:
        await self._async_stop_polling_and_close_manager()
        await self._async_stop_post_command_state_watchers()
        await self._async_close_api()

    async def _async_close_api(self) -> None:
        async with self._action_lock:
            close = getattr(self.vehicle_manager.api, "close", None)
            if callable(close):
                await self._async_complete_operation(
                    self.hass.async_add_executor_job(close)
                )

    async def _async_update_data(self) -> dict[str, Any]:
        if getattr(self, "_closing", False):
            return self.data
        if self._action_lock.locked():
            return self.data
        async with self._action_lock:
            return await self._async_update_data_unlocked()

    async def _async_update_data_unlocked(self) -> dict[str, Any]:
        return await self._async_complete_operation(self._async_poll_data())

    async def async_update_all(self) -> None:
        """Update vehicle data while preserving Japan's operation lock."""
        async with self._async_region_operation():
            await self._async_complete_operation(self._async_update_all_unlocked())

    async def async_force_update_all(self) -> None:
        """Force refresh vehicle data while preserving Japan's operation lock."""
        self._require_force_refresh()
        async with self._async_region_operation():
            await self._async_complete_operation(
                self._async_force_update_all_unlocked()
            )

    async def async_force_refresh_vehicle(self, vehicle_id: str) -> None:
        """Force refresh a single vehicle's state."""
        self._require_force_refresh()
        async with self._async_region_operation():
            await self.async_check_and_refresh_token()
            await self._async_run_executor_job(
                self.vehicle_manager.force_refresh_vehicle_state, vehicle_id
            )
            self.async_set_updated_data(self.data)

    def get_svm_image_updated_at(self, vehicle_id: str) -> dt.datetime | None:
        """Return the capture time, or local receipt time when GPS time is absent."""
        details = self.get_cached_svm_details(vehicle_id)
        if details is None:
            return None
        return details.captured_at or self._svm_received_at.get(vehicle_id)

    def _cache_svm_details(self, vehicle_id: str, details: SVMDetails) -> None:
        """Store a completed capture and invalidate previous rendered views."""
        self._svm_details[vehicle_id] = details
        self._svm_received_at[vehicle_id] = dt_util.utcnow()
        self._svm_views.pop(vehicle_id, None)

    async def async_get_svm_details(self, vehicle_id: str) -> SVMDetails:
        """Fetch the latest cached SVM image and metadata from the API."""
        details = await self._async_run_executor_job(
            self.vehicle_manager.get_svm_details, vehicle_id
        )
        self._cache_svm_details(vehicle_id, details)
        return details

    async def async_request_svm_capture(self, vehicle_id: str) -> SVMDetails:
        """Trigger a fresh SVM capture and update the cached details."""
        self._require_command("request_svm_capture")
        async with self._async_region_operation():
            return await self._async_complete_operation(
                self._async_capture_svm_locked(vehicle_id)
            )

    async def _async_capture_svm_locked(self, vehicle_id: str) -> SVMDetails:
        """Complete capture and cache updates even if the initiating task stops."""
        await self.async_check_and_refresh_token()
        details = await self._async_run_executor_job(
            self.vehicle_manager.request_svm_capture,
            vehicle_id,
            True,  # acknowledged_warning — capture is always a user-initiated action
        )
        self._cache_svm_details(vehicle_id, details)
        self.async_set_updated_data(self.data)
        return details

    async def async_get_svm_views(self, vehicle_id: str) -> dict[str, bytes] | None:
        """Return the 5 rendered SVM views (front/rear/left/right/top) as JPEG.

        Renders once per (captured_at, dewarp switch) state: every image
        entity serves from the same dict, and a new capture or a switch
        toggle invalidates it by key change. The render runs in an executor
        thread (dewarp is CPU-bound numpy work); render errors (e.g. missing
        Pillow) propagate to the caller.
        """
        if not hasattr(self, "_svm_render_locks"):
            self._svm_render_locks = {}
            self._svm_render_diagnostics = {}
        lock = self._svm_render_locks.setdefault(vehicle_id, asyncio.Lock())
        async with lock:
            while True:
                details = self.get_cached_svm_details(vehicle_id)
                if (
                    details is None
                    or not details.image_bytes
                    or not details.image_sizes
                ):
                    self._svm_render_diagnostics[vehicle_id] = {
                        "state": "awaiting_capture"
                        if details is None
                        else "missing_image_or_layout",
                        "view_count": 0,
                    }
                    return None
                key = (
                    self.get_svm_image_updated_at(vehicle_id),
                    self.svm_dewarp_enabled(vehicle_id),
                )
                cached = self._svm_views.get(vehicle_id)
                if cached is not None and cached[0] == key:
                    return cached[1]
                self._svm_render_diagnostics[vehicle_id] = {
                    "state": "rendering",
                    "view_count": 0,
                }
                try:
                    views: dict[str, bytes] = await self._async_run_executor_job(
                        partial(render_views, details, dewarp=key[1])
                    )
                except Exception:
                    self._svm_render_diagnostics[vehicle_id] = {
                        "state": "failed",
                        "view_count": 0,
                        "failure_category": "render_failed",
                    }
                    raise HomeAssistantError(
                        "Camera image could not be rendered"
                    ) from None
                if (
                    self.get_cached_svm_details(vehicle_id) is not details
                    or self.svm_dewarp_enabled(vehicle_id) != key[1]
                ):
                    continue
                if not views:
                    self._svm_render_diagnostics[vehicle_id] = {
                        "state": "failed",
                        "view_count": 0,
                        "failure_category": "invalid_image_or_layout",
                    }
                    raise HomeAssistantError(
                        "Camera image or layout could not be decoded"
                    )
                self._svm_views[vehicle_id] = (key, views)
                self._svm_render_diagnostics[vehicle_id] = {
                    "state": "complete",
                    "view_count": len(views),
                }
                return views

    def get_svm_render_diagnostics(self) -> list[dict[str, Any]]:
        """Return local render outcomes without images or vehicle identifiers."""
        return [
            dict(summary)
            for summary in list(getattr(self, "_svm_render_diagnostics", {}).values())[
                :10
            ]
        ]

    async def async_check_and_refresh_token(self) -> None:
        """Refresh token if needed via library."""
        await self._async_run_executor_job(self.vehicle_manager.check_and_refresh_token)
        await self._async_save_token()
        self._sync_vehicle_enabled_states()

    def _post_command_watch_clock(self) -> float:
        return asyncio.get_running_loop().time()

    async def _async_wait_for_post_command_poll(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def _start_post_command_state_watch(self, vehicle_id: str) -> None:
        """Watch only already-received MQTT data; never poll or wake the car."""
        if (
            getattr(self, "_closing", False)
            or vehicle_id not in self.vehicle_manager.vehicles
            or not self.vehicle_manager.vehicles[vehicle_id].enabled
        ):
            return
        if not hasattr(self, "_post_command_watch_tasks"):
            self._post_command_watch_tasks = {}
            self._post_command_watch_deadlines = {}
            self._post_command_watch_diagnostics = {}
        self._post_command_watch_deadlines[vehicle_id] = (
            self._post_command_watch_clock() + self._POST_COMMAND_WATCH_SECONDS
        )
        existing = self._post_command_watch_tasks.get(vehicle_id)
        if existing is not None and not existing.done():
            return
        self._post_command_watch_diagnostics[vehicle_id] = {
            "state": "watching",
            "poll_count": 0,
            "updates_applied": 0,
            "read_errors": 0,
            "last_result": "awaiting_telemetry",
        }
        task = self.hass.async_create_background_task(
            self._async_watch_post_command_state(vehicle_id),
            "Japan passive vehicle-state watcher",
            eager_start=False,
        )
        self._post_command_watch_tasks[vehicle_id] = task

    async def _async_watch_post_command_state(self, vehicle_id: str) -> None:
        diagnostic = self._post_command_watch_diagnostics[vehicle_id]
        try:
            while not getattr(self, "_closing", False):
                remaining = (
                    self._post_command_watch_deadlines[vehicle_id]
                    - self._post_command_watch_clock()
                )
                if remaining <= 0:
                    diagnostic["state"] = "finished"
                    return
                if self._action_lock.locked():
                    diagnostic["last_result"] = "operation_busy"
                else:
                    async with self._action_lock:
                        if getattr(self, "_closing", False):
                            return
                        vehicle = self.vehicle_manager.vehicles.get(vehicle_id)
                        if vehicle is None:
                            diagnostic["last_result"] = "vehicle_removed"
                            return
                        if not vehicle.enabled:
                            diagnostic["last_result"] = "vehicle_disabled"
                            return
                        reader = self.vehicle_manager.apply_cached_mqtt_state
                        diagnostic["poll_count"] += 1
                        try:
                            changed = await self._async_run_executor_job(
                                reader, vehicle_id
                            )
                        except Exception:
                            diagnostic["read_errors"] += 1
                            diagnostic["last_result"] = "reader_error"
                            if diagnostic["read_errors"] == 1:
                                _LOGGER.warning(
                                    "Japan passive state update could not be applied"
                                )
                        else:
                            diagnostic["last_result"] = (
                                "newer_telemetry_applied"
                                if changed is True
                                else "no_new_telemetry"
                            )
                            if changed is True:
                                diagnostic["updates_applied"] += 1
                                self.async_set_updated_data(self.data)
                remaining = (
                    self._post_command_watch_deadlines[vehicle_id]
                    - self._post_command_watch_clock()
                )
                if remaining > 0:
                    await self._async_wait_for_post_command_poll(
                        min(self._POST_COMMAND_POLL_INTERVAL_SECONDS, remaining)
                    )
        finally:
            if diagnostic["state"] == "watching":
                diagnostic["state"] = "stopped"
            if self._post_command_watch_tasks.get(vehicle_id) is asyncio.current_task():
                self._post_command_watch_tasks.pop(vehicle_id, None)
                self._post_command_watch_deadlines.pop(vehicle_id, None)

    async def _async_stop_post_command_state_watchers(self) -> None:
        tasks = list(getattr(self, "_post_command_watch_tasks", {}).values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_post_command_state_diagnostics(self) -> list[dict[str, Any]]:
        """Expose only bounded counters, not vehicle identifiers or payloads."""
        return [
            dict(value)
            for value in list(
                getattr(self, "_post_command_watch_diagnostics", {}).values()
            )[:10]
        ]

    def _check_command_confirmation(self, status: ORDER_STATUS | None) -> None:
        """Accept a service receipt without mislabelling it vehicle confirmation."""
        if status not in (ORDER_STATUS.SUCCESS, ORDER_STATUS.ACCEPTED):
            reason = {
                ORDER_STATUS.FAILED: "the service reported failure",
                ORDER_STATUS.TIMEOUT: "no matching completion reply arrived before timeout",
                ORDER_STATUS.PENDING: "completion is still pending",
            }.get(
                status if status is not None else ORDER_STATUS.UNKNOWN,
                "the completion status is unknown",
            )
            raise _UnconfirmedCommandError(
                "The vehicle command was sent but successful completion was not confirmed: "
                + reason
            )

    async def _async_refresh_command_state(self, status: ORDER_STATUS | None) -> None:
        """Read cached state separately from service acceptance or execution."""
        try:
            await self._async_run_executor_job(
                self.vehicle_manager.update_all_vehicles_with_cached_state
            )
            self.async_set_updated_data(self.data)
        except Exception as err:
            if status in (ORDER_STATUS.SUCCESS, ORDER_STATUS.ACCEPTED):
                message = (
                    "The service accepted the request, but updated vehicle state could not be retrieved"
                    if status == ORDER_STATUS.ACCEPTED
                    else "The vehicle command completed, but updated telemetry could not be retrieved"
                )
                raise _ConfirmedCommandRefreshError(message) from err
            _LOGGER.warning("Cached status refresh after unconfirmed command failed")

    async def async_await_action_and_refresh(
        self, vehicle_id: str, action_id: str
    ) -> None:
        """Read a local service receipt, then update state without another command."""
        status = None
        try:
            status = await self._async_run_executor_job(
                self.vehicle_manager.check_action_status,
                vehicle_id,
                action_id,
                False,
                0,
            )
            self._check_command_confirmation(status)
        finally:
            try:
                await self._async_refresh_command_state(status)
            finally:
                if status == ORDER_STATUS.ACCEPTED:
                    self._start_post_command_state_watch(vehicle_id)

    async def async_await_action_and_force_refresh(
        self, vehicle_id: str, action_id: str
    ) -> None:
        """Read the Japan receipt and cache without a second vehicle command."""
        await self.async_await_action_and_refresh(vehicle_id, action_id)

    async def _async_send_action(
        self,
        vehicle_id: str,
        action_fn: Callable[[], Any],
        error_label: str,
        *,
        force_refresh: bool = False,
        command: str | None = None,
    ) -> None:
        """Send a vehicle action, wait for completion, and refresh data.

        Serializes actions with a lock to prevent DuplicateRequestError
        from the Hyundai API when commands overlap. If another action is
        already in progress, raises HomeAssistantError immediately so
        the user gets a clear message instead of a mysterious long wait.
        """
        if command is None:
            self._require_remote_commands()
        else:
            self._require_command(command)
        if force_refresh:
            self._require_force_refresh()
        if self._action_lock.locked():
            _LOGGER.warning(
                "Vehicle action '%s' rejected: another action is already in progress",
                error_label,
            )
            raise HomeAssistantError(
                "Another vehicle action is in progress. "
                "Please wait for it to complete and try again."
            )
        async with self._action_lock:
            await self._async_complete_operation(
                self._async_send_action_locked(
                    vehicle_id, action_fn, error_label, force_refresh
                )
            )

    async def _async_send_action_locked(
        self,
        vehicle_id: str,
        action_fn: Callable[[], Any],
        error_label: str,
        force_refresh: bool,
    ) -> None:
        """Hold the caller's operation lock through submission and confirmation."""
        await self.async_check_and_refresh_token()
        try:
            action_id = await self._async_run_executor_job(action_fn)
        except RequestTimeoutError as err:
            raise HomeAssistantError(
                "The service response timed out; vehicle execution is unknown. "
                "Check the vehicle state before retrying."
            ) from err
        except UnsupportedControlError as err:
            raise HomeAssistantError(
                f"Vehicle does not support this action: {err}"
            ) from err
        except Exception as err:
            raise HomeAssistantError(f"Failed to {error_label}: {err}") from err
        try:
            if force_refresh:
                await self.async_await_action_and_force_refresh(vehicle_id, action_id)
            else:
                await self.async_await_action_and_refresh(vehicle_id, action_id)
        except _ConfirmedCommandRefreshError, _UnconfirmedCommandError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                "The vehicle command was sent but successful completion was not confirmed"
            ) from err

    async def async_lock_vehicle(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.lock(vehicle_id),
            "lock vehicle",
            command="lock_action",
        )

    async def async_unlock_vehicle(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.unlock(vehicle_id),
            "unlock vehicle",
            command="lock_action",
        )

    async def async_open_charge_port(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.open_charge_port(vehicle_id),
            "open charge port",
            command="charge_port_action",
        )

    async def async_close_charge_port(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.close_charge_port(vehicle_id),
            "close charge port",
            command="charge_port_action",
        )

    async def async_start_climate(
        self, vehicle_id: str, climate_options: ClimateRequestOptions
    ) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.start_climate(vehicle_id, climate_options),
            "start climate",
            command="start_climate",
        )

    async def async_stop_climate(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.stop_climate(vehicle_id),
            "stop climate",
            command="stop_climate",
        )

    async def async_start_charge(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.start_charge(vehicle_id),
            "start charge",
            command="start_charge",
        )

    async def async_stop_charge(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.stop_charge(vehicle_id),
            "stop charge",
            command="stop_charge",
        )

    async def async_set_charge_limits(self, vehicle_id: str, ac: int, dc: int) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_charge_limits(vehicle_id, ac, dc),
            "set charge limits",
            command="set_charge_limits",
        )

    async def async_set_charging_current(self, vehicle_id: str, level: int) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_charging_current(vehicle_id, level),
            "set charging current",
            command="set_charging_current",
        )

    async def async_schedule_charging_and_climate(
        self, vehicle_id: str, schedule_options: ScheduleChargingClimateRequestOptions
    ) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.schedule_charging_and_climate(
                vehicle_id, schedule_options
            ),
            "schedule charging and climate",
            command="schedule_charging_and_climate",
        )

    async def async_start_hazard_lights(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.start_hazard_lights(vehicle_id),
            "start hazard lights",
            command="start_hazard_lights",
        )

    async def async_start_hazard_lights_and_horn(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.start_hazard_lights_and_horn(vehicle_id),
            "start hazard lights and horn",
            command="start_hazard_lights_and_horn",
        )

    async def async_start_valet_mode(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.start_valet_mode(vehicle_id),
            "start valet mode",
            command="valet_mode_action",
        )

    async def async_stop_valet_mode(self, vehicle_id: str) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.stop_valet_mode(vehicle_id),
            "stop valet mode",
            command="valet_mode_action",
        )

    async def async_set_v2l_limit(self, vehicle_id: str, limit: int) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_vehicle_to_load_discharge_limit(
                vehicle_id, limit
            ),
            "set V2L limit",
            command="set_vehicle_to_load_discharge_limit",
        )

    async def async_set_windows(
        self, vehicle_id: str, windowOptions: WindowRequestOptions
    ) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_windows_state(vehicle_id, windowOptions),
            "set windows",
            command="set_windows_state",
        )

    async def async_set_navigation(
        self, vehicle_id: str, poi_list: list[POIInfo]
    ) -> None:
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_navigation(vehicle_id, poi_list),
            "set navigation",
            command="set_navigation",
        )

    def _window_options(
        self, vehicle_id: str, state: WINDOW_STATE
    ) -> WindowRequestOptions:
        vehicle = self.vehicle_manager.vehicles[vehicle_id]
        positions = vehicle.supports.supported_window_positions
        if positions is None:
            positions = ("front_left", "front_right", "back_left", "back_right")
        return WindowRequestOptions(
            front_left=state if "front_left" in positions else None,
            front_right=state if "front_right" in positions else None,
            back_left=state if "back_left" in positions else None,
            back_right=state if "back_right" in positions else None,
        )

    async def async_open_all_windows(self, vehicle_id: str) -> None:
        options = self._window_options(vehicle_id, WINDOW_STATE.OPEN)
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_windows_state(vehicle_id, options),
            "open all windows",
            command="set_windows_state",
        )

    async def async_close_all_windows(self, vehicle_id: str) -> None:
        options = self._window_options(vehicle_id, WINDOW_STATE.CLOSED)
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_windows_state(vehicle_id, options),
            "close all windows",
            command="set_windows_state",
        )

    async def async_vent_all_windows(self, vehicle_id: str) -> None:
        options = self._window_options(vehicle_id, WINDOW_STATE.VENTILATION)
        await self._async_send_action(
            vehicle_id,
            lambda: self.vehicle_manager.set_windows_state(vehicle_id, options),
            "vent all windows",
            command="set_windows_state",
        )
