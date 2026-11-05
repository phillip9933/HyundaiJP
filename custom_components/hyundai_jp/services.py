"""Services for HyundaiJP integration."""

from datetime import time
import logging
from typing import Any

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry

from ._api import (
    ClimateRequestOptions,
    POICoord,
    POIInfo,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
)
from .const import DOMAIN, OffPeakChargingMode
from .coordinator import JapanDataUpdateCoordinator

SERVICE_UPDATE = "update"
SERVICE_FORCE_UPDATE = "force_update"
SERVICE_LOCK = "lock"
SERVICE_UNLOCK = "unlock"
SERVICE_STOP_CLIMATE = "stop_climate"
SERVICE_START_CLIMATE = "start_climate"
SERVICE_START_CHARGE = "start_charge"
SERVICE_STOP_CHARGE = "stop_charge"
SERVICE_SET_CHARGE_LIMIT = "set_charge_limits"
SERVICE_SET_CHARGING_CURRENT = "set_charging_current"
SERVICE_OPEN_CHARGE_PORT = "open_charge_port"
SERVICE_CLOSE_CHARGE_PORT = "close_charge_port"
SERVICE_SCHEDULE_CHARGING_AND_CLIMATE = "schedule_charging_and_climate"
SERVICE_START_HAZARD_LIGHTS = "start_hazard_lights"
SERVICE_START_HAZARD_LIGHTS_AND_HORN = "start_hazard_lights_and_horn"
SERVICE_START_VALET_MODE = "start_valet_mode"
SERVICE_STOP_VALET_MODE = "stop_valet_mode"
SERVICE_SET_WINDOWS = "set_windows"
SERVICE_SET_NAVIGATION = "set_navigation"
SERVICE_SET_OFF_PEAK_CHARGING = "set_off_peak_charging"
SERVICE_CAPTURE_SVM_IMAGE = "capture_svm_image"

SUPPORTED_SERVICES = (
    SERVICE_UPDATE,
    SERVICE_FORCE_UPDATE,
    SERVICE_LOCK,
    SERVICE_UNLOCK,
    SERVICE_STOP_CLIMATE,
    SERVICE_START_CLIMATE,
    SERVICE_START_CHARGE,
    SERVICE_STOP_CHARGE,
    SERVICE_SET_CHARGE_LIMIT,
    SERVICE_SET_CHARGING_CURRENT,
    SERVICE_OPEN_CHARGE_PORT,
    SERVICE_CLOSE_CHARGE_PORT,
    SERVICE_SCHEDULE_CHARGING_AND_CLIMATE,
    SERVICE_START_HAZARD_LIGHTS,
    SERVICE_START_HAZARD_LIGHTS_AND_HORN,
    SERVICE_START_VALET_MODE,
    SERVICE_STOP_VALET_MODE,
    SERVICE_SET_WINDOWS,
    SERVICE_SET_NAVIGATION,
    SERVICE_SET_OFF_PEAK_CHARGING,
    SERVICE_CAPTURE_SVM_IMAGE,
)

_LOGGER = logging.getLogger(__name__)


@callback
def async_setup_services(hass: HomeAssistant) -> bool:
    """Set up services for HyundaiJP."""

    async def async_handle_force_update(call: ServiceCall) -> None:
        coordinator = _resolve_account_target(hass, call)
        await coordinator.async_force_update_all()

    async def async_handle_update(call: ServiceCall) -> None:
        _LOGGER.debug("Cached vehicle update requested")
        coordinator = _resolve_account_target(hass, call)
        await coordinator.async_update_all()

    async def async_handle_start_climate(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        duration = call.data.get("duration")
        set_temp = call.data.get("temperature")
        climate = call.data.get("climate")
        heating = call.data.get("heating")
        defrost = call.data.get("defrost")
        front_left_seat = call.data.get("flseat")
        front_right_seat = call.data.get("frseat")
        rear_left_seat = call.data.get("rlseat")
        rear_right_seat = call.data.get("rrseat")
        steering_wheel = call.data.get("steering_wheel")

        # Confirm values are correct datatype
        if heating is not None:
            heating = int(heating)
        if front_left_seat is not None:
            front_left_seat = int(front_left_seat)
        if front_right_seat is not None:
            front_right_seat = int(front_right_seat)
        if rear_left_seat is not None:
            rear_left_seat = int(rear_left_seat)
        if rear_right_seat is not None:
            rear_right_seat = int(rear_right_seat)
        if steering_wheel is not None:
            steering_wheel = int(steering_wheel)

        climate_request_options = ClimateRequestOptions(
            duration=duration,
            set_temp=set_temp,
            climate=climate,
            heating=heating,
            defrost=defrost,
            front_left_seat=front_left_seat,
            front_right_seat=front_right_seat,
            rear_left_seat=rear_left_seat,
            rear_right_seat=rear_right_seat,
            steering_wheel=steering_wheel,
        )
        await coordinator.async_start_climate(vehicle_id, climate_request_options)

    async def async_handle_stop_climate(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_stop_climate(vehicle_id)

    async def async_handle_lock(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_lock_vehicle(vehicle_id)

    async def async_handle_unlock(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_unlock_vehicle(vehicle_id)

    async def async_handle_open_charge_port(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_open_charge_port(vehicle_id)

    async def async_handle_close_charge_port(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_close_charge_port(vehicle_id)

    async def async_handle_start_charge(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_start_charge(vehicle_id)

    async def async_handle_stop_charge(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_stop_charge(vehicle_id)

    async def async_handle_set_charge_limit(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        ac = call.data.get("ac_limit")
        dc = call.data.get("dc_limit")

        if ac is not None and dc is not None:
            await coordinator.async_set_charge_limits(vehicle_id, int(ac), int(dc))
        else:
            _LOGGER.error(
                "%s - Unable to set charge limits. Both AC and DC value required, but not provided.",
                DOMAIN,
            )

    async def async_handle_set_windows(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        window_options = WindowRequestOptions(
            front_left=call.data.get("flwindow"),
            front_right=call.data.get("frwindow"),
            back_left=call.data.get("rlwindow"),
            back_right=call.data.get("rrwindow"),
        )

        if (
            window_options.front_left is not None
            and window_options.front_right is not None
            and window_options.back_left is not None
            and window_options.back_right is not None
        ):
            await coordinator.async_set_windows(vehicle_id, window_options)
        else:
            _LOGGER.error("%s - All windows value required, but not provided.", DOMAIN)

    async def async_handle_set_charging_current(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        current_level = call.data.get("level")

        if current_level is not None:
            await coordinator.async_set_charging_current(vehicle_id, int(current_level))
        else:
            _LOGGER.error(
                "%s - Unable to set charging current. Level required, but not provided.",
                DOMAIN,
            )

    async def async_handle_schedule_charging_and_climate(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        first_departure_enabled = call.data.get("first_departure_enabled")
        first_departure_days = call.data.get("first_departure_days")
        first_departure_time = call.data.get("first_departure_time")
        second_departure_enabled = call.data.get("second_departure_enabled")
        second_departure_days = call.data.get("second_departure_days")
        second_departure_time = call.data.get("second_departure_time")
        charging_enabled = call.data.get("charging_enabled")
        off_peak_start_time = call.data.get("off_peak_start_time")
        off_peak_end_time = call.data.get("off_peak_end_time")
        off_peak_charge_only_enabled = call.data.get("off_peak_charge_only_enabled")
        climate_enabled = call.data.get("climate_enabled")
        temperature = call.data.get("temperature")
        temperature_unit = call.data.get("temperature_unit")
        defrost = call.data.get("defrost")

        # Confirm values are correct datatype
        def initialize_departure_option(
            departure_enabled: Any, departure_days: Any, departure_time: Any
        ) -> ScheduleChargingClimateRequestOptions.DepartureOptions:
            return ScheduleChargingClimateRequestOptions.DepartureOptions(
                enabled=None if departure_enabled is None else bool(departure_enabled),
                days=None
                if departure_days is None
                else [int(day) for day in departure_days],
                time=None
                if departure_time is None
                else time.fromisoformat(departure_time),
            )

        first_departure = initialize_departure_option(
            first_departure_enabled, first_departure_days, first_departure_time
        )
        second_departure = initialize_departure_option(
            second_departure_enabled, second_departure_days, second_departure_time
        )
        if charging_enabled is not None:
            charging_enabled = bool(charging_enabled)
        if off_peak_start_time is not None:
            off_peak_start_time = time.fromisoformat(off_peak_start_time)
        if off_peak_end_time is not None:
            off_peak_end_time = time.fromisoformat(off_peak_end_time)
        if off_peak_charge_only_enabled is not None:
            off_peak_charge_only_enabled = bool(off_peak_charge_only_enabled)
        if climate_enabled is not None:
            climate_enabled = bool(climate_enabled)
        if temperature is not None:
            temperature = float(temperature)
        if temperature_unit is not None:
            temperature_unit = int(temperature_unit)
        if defrost is not None:
            defrost = bool(defrost)

        schedule_options = ScheduleChargingClimateRequestOptions(
            first_departure=first_departure,
            second_departure=second_departure,
            charging_enabled=charging_enabled,
            off_peak_start_time=off_peak_start_time,
            off_peak_end_time=off_peak_end_time,
            off_peak_charge_only_enabled=off_peak_charge_only_enabled,
            climate_enabled=climate_enabled,
            temperature=temperature,
            temperature_unit=temperature_unit,
            defrost=defrost,
        )
        await coordinator.async_schedule_charging_and_climate(
            vehicle_id, schedule_options
        )

    async def async_handle_set_off_peak_charging(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        mode = call.data.get("mode")
        off_peak_start_time = call.data.get("off_peak_start_time")
        off_peak_end_time = call.data.get("off_peak_end_time")
        if off_peak_start_time is not None:
            off_peak_start_time = time.fromisoformat(off_peak_start_time)
        if off_peak_end_time is not None:
            off_peak_end_time = time.fromisoformat(off_peak_end_time)
        await coordinator.async_set_off_peak_charging(
            vehicle_id,
            mode=OffPeakChargingMode(mode) if mode is not None else None,
            start=off_peak_start_time,
            end=off_peak_end_time,
        )

    async def async_handle_start_hazard_lights(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_start_hazard_lights(vehicle_id)

    async def async_handle_start_hazard_lights_and_horn(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_start_hazard_lights_and_horn(vehicle_id)

    async def async_handle_start_valet_mode(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_start_valet_mode(vehicle_id)

    async def async_handle_stop_valet_mode(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        await coordinator.async_stop_valet_mode(vehicle_id)

    async def async_handle_set_navigation(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)
        latitude = call.data["latitude"]
        longitude = call.data["longitude"]
        name = call.data["name"]
        address = call.data.get("address", "")
        zip_code = call.data.get("zip_code", "")
        place_id = call.data.get("place_id", "")

        poi = POIInfo(
            coord=POICoord(lat=float(latitude), lon=float(longitude)),
            name=name,
            addr=address,
            zip=zip_code,
            place_id=place_id,
        )
        await coordinator.async_set_navigation(vehicle_id, [poi])

    async def async_handle_capture_svm_image(call: ServiceCall) -> None:
        coordinator, vehicle_id = _resolve_vehicle_target(hass, call)

        if not await coordinator.async_supports_svm(vehicle_id):
            raise HomeAssistantError("SVM is not available for this vehicle.")

        await coordinator.async_request_svm_capture(vehicle_id)

    services = {
        SERVICE_FORCE_UPDATE: async_handle_force_update,
        SERVICE_UPDATE: async_handle_update,
        SERVICE_START_CLIMATE: async_handle_start_climate,
        SERVICE_STOP_CLIMATE: async_handle_stop_climate,
        SERVICE_LOCK: async_handle_lock,
        SERVICE_UNLOCK: async_handle_unlock,
        SERVICE_START_CHARGE: async_handle_start_charge,
        SERVICE_STOP_CHARGE: async_handle_stop_charge,
        SERVICE_SET_CHARGE_LIMIT: async_handle_set_charge_limit,
        SERVICE_OPEN_CHARGE_PORT: async_handle_open_charge_port,
        SERVICE_CLOSE_CHARGE_PORT: async_handle_close_charge_port,
        SERVICE_SET_CHARGING_CURRENT: async_handle_set_charging_current,
        SERVICE_SCHEDULE_CHARGING_AND_CLIMATE: async_handle_schedule_charging_and_climate,
        SERVICE_START_HAZARD_LIGHTS: async_handle_start_hazard_lights,
        SERVICE_START_HAZARD_LIGHTS_AND_HORN: async_handle_start_hazard_lights_and_horn,
        SERVICE_START_VALET_MODE: async_handle_start_valet_mode,
        SERVICE_STOP_VALET_MODE: async_handle_stop_valet_mode,
        SERVICE_SET_WINDOWS: async_handle_set_windows,
        SERVICE_SET_NAVIGATION: async_handle_set_navigation,
        SERVICE_SET_OFF_PEAK_CHARGING: async_handle_set_off_peak_charging,
        SERVICE_CAPTURE_SVM_IMAGE: async_handle_capture_svm_image,
    }

    for service in SUPPORTED_SERVICES:
        hass.services.async_register(DOMAIN, service, services[service])
    return True


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the integration's services."""
    for service in SUPPORTED_SERVICES:
        hass.services.async_remove(DOMAIN, service)


def _resolve_vehicle_target(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[JapanDataUpdateCoordinator, str]:
    """Resolve one active account/vehicle pair, respecting an explicit device."""
    coordinators: dict[str, JapanDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if ATTR_DEVICE_ID not in call.data:
        if len(coordinators) == 1:
            only_coordinator = next(iter(coordinators.values()))
            vehicles = only_coordinator.vehicle_manager.vehicles
            if len(vehicles) == 1:
                return only_coordinator, next(iter(vehicles))
        raise HomeAssistantError("Select a vehicle device for this action")

    device_id = call.data[ATTR_DEVICE_ID]
    if not isinstance(device_id, str) or not device_id.strip():
        raise HomeAssistantError("Select one valid vehicle device")
    device = device_registry.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError("The selected device was not found")

    matches: list[tuple[JapanDataUpdateCoordinator, str]] = []
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.unique_id is None:
            continue
        coordinator = coordinators.get(entry.unique_id)
        if (
            coordinator is None
            or coordinator.config_entry is None
            or coordinator.config_entry.entry_id != entry_id
        ):
            continue
        # A VIN alias or an old backend id must never become a command target.
        # Match only ids owned by this currently loaded account's manager.
        matches.extend(
            (coordinator, identifier)
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier in coordinator.vehicle_manager.vehicles
        )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise HomeAssistantError("The selected device has no active HyundaiJP vehicle")
    raise HomeAssistantError(
        "The selected device matches multiple active HyundaiJP vehicles"
    )


def _resolve_account_target(
    hass: HomeAssistant, call: ServiceCall
) -> JapanDataUpdateCoordinator:
    """Account refreshes may omit a device when there is exactly one account."""
    if ATTR_DEVICE_ID in call.data:
        coordinator, _ = _resolve_vehicle_target(hass, call)
        return coordinator
    coordinators: dict[str, JapanDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if len(coordinators) == 1:
        return next(iter(coordinators.values()))
    raise HomeAssistantError(
        "Select a vehicle device to identify the HyundaiJP account"
    )
