"""Sensor for Hyundai Bluelink JP integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._api import Vehicle
from ._api.const import SEAT_STATUS
from .const import DOMAIN
from .coordinator import HyundaiJPConfigEntry, JapanDataUpdateCoordinator
from .entity import HyundaiBluelinkEntity


@dataclass(frozen=True, kw_only=True)
class HyundaiBluelinkBinarySensorEntityDescription(BinarySensorEntityDescription):
    """A class that describes custom binary sensor entities."""

    is_on: Callable[[Vehicle], bool | None] | None = None
    on_icon: str | None = None
    off_icon: str | None = None


def _raw_flag(value: int | None) -> bool | None:
    """Map a raw 0/1 status from the API to a bool, keeping None as unknown."""
    return None if value is None else bool(value)


# Library SEAT_STATUS codes meaning the heater is on: 1 "On", 6-8 the heat levels.
_SEAT_HEATER_ON = frozenset(SEAT_STATUS[code] for code in (1, 6, 7, 8))


def _seat_heater_on(status: str | None) -> bool | None:
    """Map a library seat status (a SEAT_STATUS value) to heater on/off."""
    if status is None:
        return None
    return status in _SEAT_HEATER_ON


SENSOR_DESCRIPTIONS: Final[tuple[HyundaiBluelinkBinarySensorEntityDescription, ...]] = (
    HyundaiBluelinkBinarySensorEntityDescription(
        key="engine_is_running",
        translation_key="engine_is_running",
        is_on=lambda vehicle: vehicle.engine_is_running,
        on_icon="mdi:engine",
        off_icon="mdi:engine-off",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="defrost_is_on",
        translation_key="defrost_is_on",
        is_on=lambda vehicle: vehicle.defrost_is_on,
        on_icon="mdi:car-defrost-front",
        off_icon="mdi:car-defrost-front",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="steering_wheel_heater_is_on",
        translation_key="steering_wheel_heater_is_on",
        is_on=lambda vehicle: vehicle.steering_wheel_heater_is_on,
        on_icon="mdi:steering",
        off_icon="mdi:steering",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="back_window_heater_is_on",
        translation_key="back_window_heater_is_on",
        is_on=lambda vehicle: vehicle.back_window_heater_is_on,
        on_icon="mdi:car-defrost-rear",
        off_icon="mdi:car-defrost-rear",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="side_mirror_heater_is_on",
        translation_key="side_mirror_heater_is_on",
        is_on=lambda vehicle: vehicle.side_mirror_heater_is_on,
        on_icon="mdi:car-side",
        off_icon="mdi:car-side",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_left_door_is_open",
        translation_key="front_left_door_is_open",
        is_on=lambda vehicle: vehicle.front_left_door_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_right_door_is_open",
        translation_key="front_right_door_is_open",
        is_on=lambda vehicle: vehicle.front_right_door_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="back_left_door_is_open",
        translation_key="back_left_door_is_open",
        is_on=lambda vehicle: vehicle.back_left_door_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="back_right_door_is_open",
        translation_key="back_right_door_is_open",
        is_on=lambda vehicle: vehicle.back_right_door_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="trunk_is_open",
        translation_key="trunk_is_open",
        is_on=lambda vehicle: vehicle.trunk_is_open,
        on_icon="mdi:car-back",
        off_icon="mdi:car-back",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="hood_is_open",
        translation_key="hood_is_open",
        is_on=lambda vehicle: vehicle.hood_is_open,
        on_icon="mdi:car",
        off_icon="mdi:car",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_left_window_is_open",
        translation_key="front_left_window_is_open",
        is_on=lambda vehicle: vehicle.front_left_window_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_right_window_is_open",
        translation_key="front_right_window_is_open",
        is_on=lambda vehicle: vehicle.front_right_window_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="back_left_window_is_open",
        translation_key="back_left_window_is_open",
        is_on=lambda vehicle: vehicle.back_left_window_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="back_right_window_is_open",
        translation_key="back_right_window_is_open",
        is_on=lambda vehicle: vehicle.back_right_window_is_open,
        on_icon="mdi:car-door",
        off_icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_battery_is_charging",
        translation_key="ev_battery_is_charging",
        is_on=lambda vehicle: vehicle.ev_battery_is_charging,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_battery_is_plugged_in",
        translation_key="ev_battery_is_plugged_in",
        is_on=lambda vehicle: vehicle.ev_battery_is_plugged_in,
        device_class=BinarySensorDeviceClass.PLUG,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="fuel_level_is_low",
        translation_key="fuel_level_is_low",
        is_on=lambda vehicle: vehicle.fuel_level_is_low,
        on_icon="mdi:gas-station-off",
        off_icon="mdi:gas-station",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="smart_key_battery_warning_is_on",
        translation_key="smart_key_battery_warning_is_on",
        is_on=lambda vehicle: vehicle.smart_key_battery_warning_is_on,
        on_icon="mdi:battery-alert",
        off_icon="mdi:battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="air_cleaning_is_on",
        translation_key="air_cleaning_is_on",
        is_on=lambda vehicle: vehicle.air_cleaning_is_on,
        on_icon="mdi:air-filter",
        off_icon="mdi:air-filter-off",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="auto_cut_battery_prewarning_on",
        translation_key="auto_cut_battery_prewarning_on",
        is_on=lambda vehicle: vehicle.auto_cut_battery_prewarning_on,
        on_icon="mdi:battery-alert",
        off_icon="mdi:battery-alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="hazard_lights_on",
        translation_key="hazard_lights_on",
        is_on=lambda vehicle: vehicle.hazard_lights_on,
        on_icon="mdi:hazard-lights",
        off_icon="mdi:hazard-lights",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="windshield_front_heater_is_on",
        translation_key="windshield_front_heater_is_on",
        is_on=lambda vehicle: vehicle.windshield_front_heater_is_on,
        on_icon="mdi:heat-wave",
        off_icon="mdi:heat-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="washer_fluid_warning_is_on",
        translation_key="washer_fluid_warning_is_on",
        is_on=lambda vehicle: vehicle.washer_fluid_warning_is_on,
        on_icon="mdi:wiper-wash-alert",
        off_icon="mdi:wiper-wash",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="tire_pressure_all_warning_is_on",
        translation_key="tire_pressure_all_warning_is_on",
        is_on=lambda vehicle: vehicle.tire_pressure_all_warning_is_on,
        on_icon="mdi:car-tire-alert",
        off_icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="tire_pressure_rear_left_warning_is_on",
        translation_key="tire_pressure_rear_left_warning_is_on",
        is_on=lambda vehicle: vehicle.tire_pressure_rear_left_warning_is_on,
        on_icon="mdi:car-tire-alert",
        off_icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="tire_pressure_front_left_warning_is_on",
        translation_key="tire_pressure_front_left_warning_is_on",
        is_on=lambda vehicle: vehicle.tire_pressure_front_left_warning_is_on,
        on_icon="mdi:car-tire-alert",
        off_icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="tire_pressure_front_right_warning_is_on",
        translation_key="tire_pressure_front_right_warning_is_on",
        is_on=lambda vehicle: vehicle.tire_pressure_front_right_warning_is_on,
        on_icon="mdi:car-tire-alert",
        off_icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="tire_pressure_rear_right_warning_is_on",
        translation_key="tire_pressure_rear_right_warning_is_on",
        is_on=lambda vehicle: vehicle.tire_pressure_rear_right_warning_is_on,
        on_icon="mdi:car-tire-alert",
        off_icon="mdi:car-tire-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="air_control_is_on",
        translation_key="air_control_is_on",
        is_on=lambda vehicle: vehicle.air_control_is_on,
        on_icon="mdi:air-conditioner",
        off_icon="mdi:air-conditioner",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_charge_port_door_is_open",
        translation_key="ev_charge_port_door_is_open",
        is_on=lambda vehicle: vehicle.ev_charge_port_door_is_open,
        on_icon="mdi:ev-station",
        off_icon="mdi:ev-station",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="brake_fluid_warning_is_on",
        translation_key="brake_fluid_warning_is_on",
        is_on=lambda vehicle: vehicle.brake_fluid_warning_is_on,
        on_icon="mdi:car-brake-alert",
        off_icon="mdi:car-brake-fluid-level",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="sunroof_is_open",
        translation_key="sunroof_is_open",
        is_on=lambda vehicle: vehicle.sunroof_is_open,
        on_icon="mdi:window-closed",
        off_icon="mdi:window-closed",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="accessory_on",
        translation_key="accessory_on",
        is_on=lambda vehicle: vehicle.accessory_on,
        on_icon="mdi:car-key",
        off_icon="mdi:car-off",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ign3",
        translation_key="ign3",
        is_on=lambda vehicle: vehicle.ign3,
        on_icon="mdi:key",
        off_icon="mdi:car-off",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="remote_ignition",
        translation_key="remote_ignition",
        is_on=lambda vehicle: vehicle.remote_ignition,
        on_icon="mdi:remote",
        off_icon="mdi:remote-off",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="transmission_condition",
        translation_key="transmission_condition",
        is_on=lambda vehicle: _raw_flag(vehicle.transmission_condition),
        on_icon="mdi:car-shift-pattern",
        off_icon="mdi:car",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="sleep_mode_check",
        translation_key="sleep_mode_check",
        is_on=lambda vehicle: vehicle.sleep_mode_check,
        on_icon="mdi:sleep",
        off_icon="mdi:sleep-off",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="headlamp_status",
        translation_key="headlamp_status",
        is_on=lambda vehicle: _raw_flag(vehicle.headlamp_status),
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="headlamp_left_low",
        translation_key="headlamp_left_low",
        is_on=lambda vehicle: vehicle.headlamp_left_low,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="headlamp_right_low",
        translation_key="headlamp_right_low",
        is_on=lambda vehicle: vehicle.headlamp_right_low,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="headlamp_left_high",
        translation_key="headlamp_left_high",
        is_on=lambda vehicle: vehicle.headlamp_left_high,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="headlamp_right_high",
        translation_key="headlamp_right_high",
        is_on=lambda vehicle: vehicle.headlamp_right_high,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="headlamp_left_bifunc",
        translation_key="headlamp_left_bifunc",
        is_on=lambda vehicle: vehicle.headlamp_left_bifunc,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="headlamp_right_bifunc",
        translation_key="headlamp_right_bifunc",
        is_on=lambda vehicle: vehicle.headlamp_right_bifunc,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="stop_lamp_left",
        translation_key="stop_lamp_left",
        is_on=lambda vehicle: vehicle.stop_lamp_left,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="stop_lamp_right",
        translation_key="stop_lamp_right",
        is_on=lambda vehicle: vehicle.stop_lamp_right,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="turn_signal_left_front",
        translation_key="turn_signal_left_front",
        is_on=lambda vehicle: vehicle.turn_signal_left_front,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="turn_signal_right_front",
        translation_key="turn_signal_right_front",
        is_on=lambda vehicle: vehicle.turn_signal_right_front,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="turn_signal_left_rear",
        translation_key="turn_signal_left_rear",
        is_on=lambda vehicle: vehicle.turn_signal_left_rear,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="turn_signal_right_rear",
        translation_key="turn_signal_right_rear",
        is_on=lambda vehicle: vehicle.turn_signal_right_rear,
        on_icon="mdi:lightbulb-alert-outline",
        off_icon="mdi:lightbulb-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="is_locked",
        translation_key="is_locked",
        is_on=lambda vehicle: not vehicle.is_locked,
        device_class=BinarySensorDeviceClass.LOCK,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Seat heater sensors derived from seat status strings
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_left_seat_heater_on",
        translation_key="front_left_seat_heater_on",
        is_on=lambda vehicle: _seat_heater_on(vehicle.front_left_seat_status),
        on_icon="mdi:seat-heater",
        off_icon="mdi:seat-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_right_seat_heater_on",
        translation_key="front_right_seat_heater_on",
        is_on=lambda vehicle: _seat_heater_on(vehicle.front_right_seat_status),
        on_icon="mdi:seat-heater",
        off_icon="mdi:seat-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="rear_left_seat_heater_on",
        translation_key="rear_left_seat_heater_on",
        is_on=lambda vehicle: _seat_heater_on(vehicle.rear_left_seat_status),
        on_icon="mdi:seat-heater",
        off_icon="mdi:seat-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="rear_right_seat_heater_on",
        translation_key="rear_right_seat_heater_on",
        is_on=lambda vehicle: _seat_heater_on(vehicle.rear_right_seat_status),
        on_icon="mdi:seat-heater",
        off_icon="mdi:seat-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_left_door_is_locked",
        translation_key="front_left_door_is_locked",
        device_class=BinarySensorDeviceClass.LOCK,
        is_on=lambda vehicle: not vehicle.front_left_door_is_locked,
        on_icon="mdi:lock-open",
        off_icon="mdi:lock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="front_right_door_is_locked",
        translation_key="front_right_door_is_locked",
        device_class=BinarySensorDeviceClass.LOCK,
        is_on=lambda vehicle: not vehicle.front_right_door_is_locked,
        on_icon="mdi:lock-open",
        off_icon="mdi:lock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="back_left_door_is_locked",
        translation_key="back_left_door_is_locked",
        device_class=BinarySensorDeviceClass.LOCK,
        is_on=lambda vehicle: not vehicle.back_left_door_is_locked,
        on_icon="mdi:lock-open",
        off_icon="mdi:lock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="back_right_door_is_locked",
        translation_key="back_right_door_is_locked",
        device_class=BinarySensorDeviceClass.LOCK,
        is_on=lambda vehicle: not vehicle.back_right_door_is_locked,
        on_icon="mdi:lock-open",
        off_icon="mdi:lock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_battery_winter_mode",
        translation_key="ev_battery_winter_mode",
        icon="mdi:snowflake-thermometer",
        is_on=lambda vehicle: vehicle.ev_battery_winter_mode,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_battery_precondition_enabled",
        translation_key="ev_battery_precondition_enabled",
        icon="mdi:battery-charging-high",
        is_on=lambda vehicle: vehicle.ev_battery_precondition_enabled,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_battery_heating_state",
        translation_key="ev_battery_heating_state",
        icon="mdi:fire",
        is_on=lambda vehicle: vehicle.ev_battery_heating_state,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_v2l_status",
        translation_key="ev_v2l_status",
        icon="mdi:ev-station",
        is_on=lambda vehicle: vehicle.ev_v2l_status,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="ev_v2x_status",
        translation_key="ev_v2x_status",
        icon="mdi:ev-station",
        is_on=lambda vehicle: vehicle.ev_v2x_status,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="oil_level_warning_is_on",
        translation_key="oil_level_warning_is_on",
        is_on=lambda vehicle: vehicle.oil_level_warning_is_on,
        on_icon="mdi:oil",
        off_icon="mdi:oil",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HyundaiBluelinkBinarySensorEntityDescription(
        key="battery_auxiliary_fail_warning_is_on",
        translation_key="battery_auxiliary_fail_warning_is_on",
        is_on=lambda vehicle: vehicle.battery_auxiliary_fail_warning_is_on,
        on_icon="mdi:car-battery",
        off_icon="mdi:car-battery",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HyundaiJPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary_sensor platform."""
    coordinator = config_entry.runtime_data
    entities: list[HyundaiBluelinkBinarySensor] = []
    for vehicle_id in coordinator.vehicle_manager.vehicles:
        vehicle: Vehicle = coordinator.vehicle_manager.vehicles[vehicle_id]
        for description in SENSOR_DESCRIPTIONS:
            if getattr(vehicle, description.key, None) is not None:
                entities.append(
                    HyundaiBluelinkBinarySensor(coordinator, description, vehicle)
                )
    async_add_entities(entities)


PARALLEL_UPDATES = 0


class HyundaiBluelinkBinarySensor(BinarySensorEntity, HyundaiBluelinkEntity):
    """Hyundai Bluelink JP binary sensor class."""

    def __init__(
        self,
        coordinator: JapanDataUpdateCoordinator,
        description: HyundaiBluelinkBinarySensorEntityDescription,
        vehicle: Vehicle,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, vehicle)
        self.entity_description: HyundaiBluelinkBinarySensorEntityDescription = (
            description
        )
        self._attr_unique_id = f"{DOMAIN}_{vehicle.id}_{description.key}"
        if description.entity_category:
            self._attr_entity_category = description.entity_category

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.entity_description.is_on is not None:
            return self.entity_description.is_on(self.vehicle)
        return None

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        return (
            self.entity_description.on_icon
            if self.is_on
            else self.entity_description.off_icon
        )
