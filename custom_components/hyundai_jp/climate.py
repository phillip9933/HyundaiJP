"""Switches for Hyundai Bluelink JP integration."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityDescription
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from ._api import ClimateRequestOptions, Vehicle
from ._api.utils import flag_or_none
from .const import DOMAIN
from .coordinator import JapanDataUpdateCoordinator
from .entity import HyundaiBluelinkEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate platform."""
    coordinator = hass.data[DOMAIN][config_entry.unique_id]
    if not (
        coordinator.supports_command("start_climate")
        and coordinator.supports_command("stop_climate")
    ):
        async_add_entities([])
        return
    entities = []
    for vehicle in coordinator.vehicle_manager.vehicles.values():
        if vehicle.air_control_is_on is not None:
            entities.append(
                HyundaiBluelinkCarClimateControlSwitch(coordinator, vehicle)
            )
    async_add_entities(entities)


PARALLEL_UPDATES = 1


class HyundaiBluelinkCarClimateControlSwitch(HyundaiBluelinkEntity, ClimateEntity):
    """Hyundai Bluelink JP Car Climate Control."""

    vehicle: Vehicle

    _TEMPERATURE_CONFIRMATION_SECONDS = 120

    def __init__(
        self,
        coordinator: JapanDataUpdateCoordinator,
        vehicle: Vehicle,
    ) -> None:
        """Keep explicit user choices separate from the latest vehicle data."""
        super().__init__(coordinator, vehicle)
        self.entity_description = ClimateEntityDescription(
            key="climate_control",
            translation_key="climate_control",
            icon="mdi:air-conditioner",
            unit_of_measurement=UnitOfTemperature.CELSIUS,
        )
        self._attr_unique_id = f"{DOMAIN}_{vehicle.id}_climate_control"
        self._request_lock = asyncio.Lock()
        self._requested_temperature: float | None = None
        self._temperature_confirmation_deadline: float | None = None
        self._temperature_expiry_handle: asyncio.TimerHandle | None = None
        self._last_known_temperature = self._vehicle_temperature()
        self.async_on_remove(self._cancel_temperature_expiry)

    def _vehicle_temperature(self) -> float | None:
        """Normalize known telemetry to the Celsius accepted by Japan commands."""
        value = self.vehicle.air_temperature
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            return None
        if not math.isfinite(value):
            return None
        unit = self.vehicle._air_temperature_unit
        if unit in ("F", UnitOfTemperature.FAHRENHEIT):
            return TemperatureConverter.convert(
                value, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS
            )
        if unit in (None, "C", UnitOfTemperature.CELSIUS):
            return float(value)
        return None

    def _sync_temperature_choice(self) -> None:
        """Settle a sent choice only on a matching reading or bounded expiry."""
        current = self._vehicle_temperature()
        if current is not None:
            self._last_known_temperature = current
        deadline = self._temperature_confirmation_deadline
        if deadline is not None and (
            current == self._requested_temperature or time.monotonic() >= deadline
        ):
            self._requested_temperature = None
            self._temperature_confirmation_deadline = None
            self._cancel_temperature_expiry()

    def _cancel_temperature_expiry(self) -> None:
        if self._temperature_expiry_handle is not None:
            self._temperature_expiry_handle.cancel()
            self._temperature_expiry_handle = None

    def _schedule_temperature_expiry(self) -> None:
        self._cancel_temperature_expiry()
        deadline = self._temperature_confirmation_deadline
        if deadline is not None:
            self._temperature_expiry_handle = asyncio.get_running_loop().call_later(
                max(0, deadline - time.monotonic()), self._expire_temperature_choice
            )

    def _expire_temperature_choice(self) -> None:
        """Publish expiry even when no new car telemetry arrives."""
        self._temperature_expiry_handle = None
        self._requested_temperature = None
        self._temperature_confirmation_deadline = None
        self._sync_temperature_choice()
        self.async_write_ha_state()

    @property
    def native_temperature_unit(self) -> str:
        """Japan commands are Celsius-native; HA converts display and input."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_current_temperature(self) -> float | None:
        """The API reports a setpoint, not an observed cabin temperature."""
        return None

    @property
    def native_target_temperature(self) -> float | None:
        """Show the same explicit choice that the next request will send."""
        self._sync_temperature_choice()
        if self._requested_temperature is not None:
            return self._requested_temperature
        return self._last_known_temperature

    @property
    def target_temperature_step(self) -> float:
        return 0.5

    @property
    def min_temp(self) -> float:
        bounds = self.vehicle.supports.climate_temperature_range
        return bounds[0] if bounds is not None else 14

    @property
    def max_temp(self) -> float:
        bounds = self.vehicle.supports.climate_temperature_range
        return bounds[1] if bounds is not None else 30

    def _validated_temperature(self, value: Any) -> float:
        """Quantize HA's converted input to the supported half-degree step."""
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
        ):
            raise HomeAssistantError(
                "Choose a target temperature before starting climate"
            )
        if not self.min_temp <= value <= self.max_temp:
            raise HomeAssistantError(
                "Target temperature is outside the vehicle's supported range"
            )
        return math.floor(float(value) * 2 + 0.5) / 2

    def _build_climate_request(self) -> ClimateRequestOptions:
        """Use current heater/defrost state, never a startup snapshot."""
        selected = self.native_target_temperature
        temperature = self._validated_temperature(selected)
        if selected != temperature:
            raise HomeAssistantError(
                "Choose a target temperature in the supported half-degree steps"
            )
        wheel = flag_or_none(self.vehicle.steering_wheel_heater_is_on)
        rear = flag_or_none(self.vehicle.back_window_heater_is_on)
        heating = None
        if isinstance(wheel, bool) and isinstance(rear, bool):
            heating = 1 if wheel and rear else 2 if rear else 3 if wheel else 0
        defrost = flag_or_none(self.vehicle.defrost_is_on)
        return ClimateRequestOptions(
            set_temp=temperature,
            temp_unit=0,
            climate=True,
            heating=heating,
            defrost=defrost if isinstance(defrost, bool) else None,
        )

    def _mark_temperature_submitted(self) -> None:
        if self._requested_temperature is not None:
            self._temperature_confirmation_deadline = (
                time.monotonic() + self._TEMPERATURE_CONFIRMATION_SECONDS
            )
            self._schedule_temperature_expiry()

    def _require_idle_request(self) -> None:
        if self._request_lock.locked():
            raise HomeAssistantError("Another climate request is in progress")

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Get the configured climate control operation mode."""
        if self.vehicle.air_control_is_on is None:
            return None
        if not self.vehicle.air_control_is_on:
            return HVACMode.OFF

        # The API exposes no cabin temperature, so heat-vs-cool is not
        # derivable; the car decides the direction itself from the setpoint.
        return HVACMode.AUTO

    @property
    def hvac_action(self) -> HVACAction | None:
        """Get what the in-car climate control is currently doing.

        Not derivable: the API has no cabin temperature to compare a
        setpoint against (issue #1871).
        """
        if not self.vehicle.air_control_is_on:
            return HVACAction.OFF
        return None

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Supported in-car climate control modes."""
        return [
            HVACMode.OFF,
            # Heat-vs-cool is decided by the car, not selectable
            HVACMode.AUTO,
        ]

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Supported in-car climate control features."""
        return (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    async def async_turn_on(self) -> None:
        """Turn the in-car climate control on."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    def _handle_coordinator_update(self) -> None:
        self._sync_temperature_choice()
        super()._handle_coordinator_update()

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_stop_climate(self.vehicle.id)
        elif hvac_mode in (HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL):
            options = self._build_climate_request()
            try:
                await self.coordinator.async_start_climate(self.vehicle.id, options)
            except asyncio.CancelledError:
                self._mark_temperature_submitted()
                raise
            self._mark_temperature_submitted()
        else:
            raise HomeAssistantError("Unsupported climate mode")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._require_idle_request()
        async with self._request_lock:
            await self._async_set_hvac_mode(hvac_mode)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Keep a draft while off; serialize a complete stop/wait/start while on."""
        self._require_idle_request()
        async with self._request_lock:
            previous = (
                self._requested_temperature,
                self._temperature_confirmation_deadline,
            )
            old_temperature = self.native_target_temperature
            value = kwargs.get(ATTR_TEMPERATURE)
            if value is not None:
                self._requested_temperature = self._validated_temperature(value)
                self._temperature_confirmation_deadline = None
                self._cancel_temperature_expiry()
            elif kwargs.get(ATTR_HVAC_MODE) is None:
                raise HomeAssistantError("Provide a target temperature")
            try:
                hvac_mode = kwargs.get(ATTR_HVAC_MODE)
                if (
                    self.vehicle.air_control_is_on is True
                    and value is not None
                    and old_temperature != self._requested_temperature
                    and hvac_mode in (None, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL)
                ):
                    options = self._build_climate_request()
                    await self.coordinator.async_change_climate_temperature(
                        self.vehicle.id, options
                    )
                    self._mark_temperature_submitted()
                elif hvac_mode is not None:
                    await self._async_set_hvac_mode(hvac_mode)
            except asyncio.CancelledError:
                # Coordinator cancellation waits for its owned operation. Keep
                # any submitted target bounded until matching telemetry arrives.
                self._mark_temperature_submitted()
                raise
            except Exception:
                self._requested_temperature, self._temperature_confirmation_deadline = (
                    previous
                )
                self._schedule_temperature_expiry()
                raise
        self.async_write_ha_state()

    # Home Assistant before 2026.11 reads the legacy property names.
    if not hasattr(ClimateEntity, "native_temperature_unit"):
        temperature_unit = native_temperature_unit
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature
