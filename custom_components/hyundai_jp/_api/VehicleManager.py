"""VehicleManager.py"""

import datetime as dt
from datetime import timedelta
import logging
from threading import RLock
from typing import Any

from .client import HyundaiBluelinkApi
from .const import (
    CHARGE_PORT_ACTION,
    DOMAIN,
    ORDER_STATUS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
)
from .exceptions import AuthenticationError
from .options import (
    ClimateRequestOptions,
    POIInfo,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
)
from .svm import SVMDetails
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class VehicleManager:
    def __init__(
        self,
        username: str,
        password: str,
        pin: str,
        geocode_api_enable: bool = False,
        geocode_api_use_email: bool = False,
        token: Token | None = None,
    ):
        self.username: str = username
        self.password: str = password
        self.geocode_api_enable: bool = geocode_api_enable
        self.geocode_api_use_email: bool = geocode_api_use_email
        self.pin: str = pin

        self.api = HyundaiBluelinkApi()

        self.token: Token | None = token
        self.vehicles: dict[str, Vehicle] = {}
        self._lifecycle_lock = RLock()
        self._closed = False

    def close(self) -> None:
        """Release adapter-owned connections once; safe to repeat."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
        self.api.close()

    def login(self) -> bool:
        """Sign in and initialize Japan vehicles when a token is returned."""
        result = self.api.login(
            username=self.username,
            password=self.password,
            pin=self.pin,
        )
        if isinstance(result, Token):
            self.token = result
            self.initialize_vehicles()
            return True
        return False

    def get_token(self) -> Token:
        """Return an authenticated token; never submit a request before login."""
        if self.token is None:
            raise AuthenticationError("Sign in to Hyundai Bluelink JP first")
        return self.token

    def invalidate_access_token(self) -> None:
        """Expire a rejected access token without discarding refresh identity."""
        if self.token is not None:
            self.token.valid_until = dt.datetime.min.replace(tzinfo=dt.UTC)

    def initialize_vehicles(self) -> None:
        if len(self.vehicles) > 0:
            _LOGGER.warning(
                "Vehicles already initialized, this will re-initialize and cause data loss mapping errors"
            )
        vehicles = self.api.get_vehicles(self.get_token())
        for vehicle in vehicles:
            self.vehicles[vehicle.id] = vehicle

    @property
    def supported_commands(self) -> frozenset[str] | None:
        """Command names implemented by the bundled Japan client."""
        return self.api.supported_commands

    def get_local_diagnostics(self) -> dict[str, Any]:
        """Read protocol diagnostics without contacting the service or vehicle."""
        return self.api.get_local_diagnostics()

    def apply_cached_mqtt_state(self, vehicle_id: str) -> bool:
        """Apply locally received telemetry without refreshing authentication."""
        return self.api.apply_cached_mqtt_state(self.get_vehicle(vehicle_id))

    def get_vehicle(self, vehicle_id: str) -> Vehicle:
        return self.vehicles[vehicle_id]

    def update_all_vehicles_with_cached_state(self) -> None:
        for vehicle_id in self.vehicles:
            self.update_vehicle_with_cached_state(vehicle_id)

    def update_vehicle_with_cached_state(self, vehicle_id: str) -> None:
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.enabled:
            vehicle.last_scanned_at = dt.datetime.now(dt.UTC)
            try:
                self.api.update_vehicle_with_cached_state(self.get_token(), vehicle)
            except AuthenticationError:
                self.invalidate_access_token()
                raise
            if self.geocode_api_enable is True:
                self.api.update_geocoded_location(
                    token=self.get_token(),
                    vehicle=vehicle,
                    use_email=self.geocode_api_use_email,
                )
        else:
            _LOGGER.debug(f"{DOMAIN} - Vehicle Disabled, skipping.")

    def check_and_force_update_vehicle(
        self, force_refresh_interval: int, vehicle_id: str
    ) -> None:
        # Force refresh only if current data is older than the value bassed in seconds.
        # Otherwise runs a cached update.
        started_at_utc: dt.datetime = dt.datetime.now(dt.UTC)
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.last_updated_at is not None:
            _LOGGER.debug(
                f"{DOMAIN} - Time differential in seconds: {(started_at_utc - vehicle.last_updated_at).total_seconds()}"
            )
            if (
                started_at_utc - vehicle.last_updated_at
            ).total_seconds() > force_refresh_interval:
                self.force_refresh_vehicle_state(vehicle_id)
            else:
                self.update_vehicle_with_cached_state(vehicle_id)
        else:
            self.update_vehicle_with_cached_state(vehicle_id)

    def force_refresh_vehicle_state(self, vehicle_id: str) -> None:
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.enabled:
            vehicle.last_scanned_at = dt.datetime.now(dt.UTC)
            try:
                self.api.force_refresh_vehicle_state(self.get_token(), vehicle)
            except AuthenticationError:
                # A rejected command must not be replayed automatically.
                self.invalidate_access_token()
                raise
        else:
            _LOGGER.debug(f"{DOMAIN} - Vehicle Disabled, skipping.")

    def get_svm_details(self, vehicle_id: str) -> SVMDetails:
        """Return the latest cached SVM composite image and metadata.

        Reads this account's completed capture cache without waking the car.
        """
        return self.api.get_svm_details(self.get_token(), self.get_vehicle(vehicle_id))

    def request_svm_capture(
        self, vehicle_id: str, acknowledged_warning: bool
    ) -> SVMDetails:
        """Trigger a fresh SVM capture and return the resulting image.

        ``acknowledged_warning`` must be True; the caller must explicitly
        acknowledge the safety warning before triggering a capture.
        """
        return self.api.request_svm_capture(
            self.get_token(), self.get_vehicle(vehicle_id), acknowledged_warning
        )

    def check_and_refresh_token(self) -> bool:
        if self.token is None:
            if self.login() is True:
                if len(self.vehicles) == 0:
                    self.initialize_vehicles()
                return True
            else:
                raise AuthenticationError("Japan sign-in did not return a token")
        now_utc = dt.datetime.now(dt.UTC)
        grace_period = timedelta(seconds=10)
        min_supported_datetime = dt.datetime.min.replace(tzinfo=dt.UTC)
        valid_until = self.token.valid_until
        token_expired = False
        if not isinstance(valid_until, dt.datetime):
            token_expired = True
        else:
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=dt.UTC)
            if valid_until <= min_supported_datetime + grace_period:
                token_expired = True
            else:
                token_expired = valid_until - grace_period <= now_utc
        if token_expired:
            _LOGGER.debug(f"{DOMAIN} - Refresh token expired")
            result = self.api.refresh_access_token(self.token)
            if isinstance(result, Token):
                self.token = result
                # Temp correction to fix bad data due to a bug.
                if self.token.pin != self.pin:
                    self.token.pin = self.pin
                if len(self.vehicles) == 0:
                    self.initialize_vehicles()
            else:
                raise AuthenticationError("Japan refresh did not return a token")
            return True
        if len(self.vehicles) == 0:
            self.initialize_vehicles()
        return False

    def validate_climate_options(
        self, vehicle_id: str, options: ClimateRequestOptions
    ) -> None:
        """Preflight a restart before sending its separate stop command."""
        self.api.build_climate_payload(self.get_vehicle(vehicle_id), options)

    def start_climate(
        self, vehicle_id: str, options: ClimateRequestOptions
    ) -> str | None:
        return self.api.start_climate(
            self.get_token(), self.get_vehicle(vehicle_id), options
        )

    def stop_climate(self, vehicle_id: str) -> str | None:
        return self.api.stop_climate(self.get_token(), self.get_vehicle(vehicle_id))

    def lock(self, vehicle_id: str) -> str | None:
        return self.api.lock_action(
            self.get_token(), self.get_vehicle(vehicle_id), VEHICLE_LOCK_ACTION.LOCK
        )

    def unlock(self, vehicle_id: str) -> str | None:
        return self.api.lock_action(
            self.get_token(),
            self.get_vehicle(vehicle_id),
            VEHICLE_LOCK_ACTION.UNLOCK,
        )

    def start_charge(self, vehicle_id: str) -> str | None:
        return self.api.start_charge(self.get_token(), self.get_vehicle(vehicle_id))

    def stop_charge(self, vehicle_id: str) -> str | None:
        return self.api.stop_charge(self.get_token(), self.get_vehicle(vehicle_id))

    def start_hazard_lights(self, vehicle_id: str) -> str | None:
        return self.api.start_hazard_lights(
            self.get_token(), self.get_vehicle(vehicle_id)
        )

    def start_hazard_lights_and_horn(self, vehicle_id: str) -> str | None:
        return self.api.start_hazard_lights_and_horn(
            self.get_token(), self.get_vehicle(vehicle_id)
        )

    def set_charge_limits(self, vehicle_id: str, ac: int, dc: int) -> str | None:
        return self.api.set_charge_limits(
            self.get_token(), self.get_vehicle(vehicle_id), ac, dc
        )

    def set_charging_current(self, vehicle_id: str, level: int) -> str:
        return self.api.set_charging_current(
            self.get_token(), self.get_vehicle(vehicle_id), level
        )

    def set_windows_state(self, vehicle_id: str, options: WindowRequestOptions) -> str:
        return self.api.set_windows_state(
            self.get_token(), self.get_vehicle(vehicle_id), options
        )

    def check_action_status(
        self,
        vehicle_id: str,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 120,
    ) -> ORDER_STATUS:
        """
        Check for the status of a sent action/command.

        Actions can have 4 states:
        - pending: request sent to vehicle, waiting for response
        - success: vehicle confirmed that the action was performed
        - fail: vehicle could not perform the action
                (most likely because a condition was not met)
        - vehicle timeout: request sent to vehicle, no response received.

        In case of timeout, the API can return "pending" for up to 2 minutes before
        it returns a final state.

        :param vehicle_id: ID of the vehicle
        :param action_id: ID of the action
        :param synchronous: Whether to wait for pending actions to reach a final
                            state (success/fail/timeout)
        :param timeout:
            Time in seconds to wait for pending actions to reach a final state.
        :return: status of the order
        """
        return self.api.check_action_status(
            self.get_token(),
            self.get_vehicle(vehicle_id),
            action_id,
            synchronous,
            timeout,
        )

    def open_charge_port(self, vehicle_id: str) -> str:
        return self.api.charge_port_action(
            self.get_token(), self.get_vehicle(vehicle_id), CHARGE_PORT_ACTION.OPEN
        )

    def close_charge_port(self, vehicle_id: str) -> str:
        return self.api.charge_port_action(
            self.get_token(), self.get_vehicle(vehicle_id), CHARGE_PORT_ACTION.CLOSE
        )

    def disable_vehicle(self, vehicle_id: str) -> None:
        self.get_vehicle(vehicle_id).enabled = False

    def enable_vehicle(self, vehicle_id: str) -> None:
        self.get_vehicle(vehicle_id).enabled = True

    def schedule_charging_and_climate(
        self, vehicle_id: str, options: ScheduleChargingClimateRequestOptions
    ) -> str:
        """Set scheduled charging and/or climate for the vehicle.

        ``None`` option fields mean "leave unchanged": the library resolves
        them from the vehicle's current settings. On ccNC/EV5-appMode EVs a
        scope whose options are all ``None`` skips its endpoint entirely;
        calling with no field set raises ``ValueError``.
        """
        return self.api.schedule_charging_and_climate(
            self.get_token(), self.get_vehicle(vehicle_id), options
        )

    def start_valet_mode(self, vehicle_id: str) -> str:
        return self.api.valet_mode_action(
            self.get_token(), self.get_vehicle(vehicle_id), VALET_MODE_ACTION.ACTIVATE
        )

    def stop_valet_mode(self, vehicle_id: str) -> str:
        return self.api.valet_mode_action(
            self.get_token(), self.get_vehicle(vehicle_id), VALET_MODE_ACTION.DEACTIVATE
        )

    def set_vehicle_to_load_discharge_limit(self, vehicle_id: str, limit: int) -> str:
        return self.api.set_vehicle_to_load_discharge_limit(
            self.get_token(), self.get_vehicle(vehicle_id), limit
        )

    def set_navigation(self, vehicle_id: str, poi_list: list[POIInfo]) -> str:
        return self.api.set_navigation(
            self.get_token(), self.get_vehicle(vehicle_id), poi_list
        )
