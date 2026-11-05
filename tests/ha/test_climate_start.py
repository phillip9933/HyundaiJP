"""Climate requests use explicit targets and current telemetry, without I/O."""

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.climate import HyundaiBluelinkCarClimateControlSwitch
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator


def _entity(air_control_is_on: bool) -> HyundaiBluelinkCarClimateControlSwitch:
    """A climate entity over a vehicle in the given air-control state."""
    vehicle = Vehicle(id="v1", name="test", model="test")
    vehicle.air_control_is_on = air_control_is_on
    # The setter takes a (value, unit) pair.
    vehicle.air_temperature = (21, "C")
    vehicle.defrost_is_on = False
    vehicle.steering_wheel_heater_is_on = False
    vehicle.back_window_heater_is_on = False

    coordinator = MagicMock(spec=JapanDataUpdateCoordinator)
    coordinator.async_start_climate = AsyncMock()
    coordinator.async_stop_climate = AsyncMock()

    entity = HyundaiBluelinkCarClimateControlSwitch(coordinator, vehicle)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


async def test_start_requests_air_control_on_while_climate_is_off() -> None:
    """Starting from the off state must send climate=True, not the stale False."""
    entity = _entity(air_control_is_on=False)

    await entity.async_set_hvac_mode(HVACMode.HEAT)

    _vehicle_id, options = entity.coordinator.async_start_climate.call_args.args
    assert options.climate is True


async def test_cool_also_requests_air_control_on() -> None:
    """The car has no mode of its own, so COOL is the same request as HEAT."""
    entity = _entity(air_control_is_on=False)

    await entity.async_set_hvac_mode(HVACMode.COOL)

    _vehicle_id, options = entity.coordinator.async_start_climate.call_args.args
    assert options.climate is True


async def test_off_stops_climate_and_does_not_start_it() -> None:
    """Turning off must reach stop_climate only."""
    entity = _entity(air_control_is_on=True)

    await entity.async_set_hvac_mode(HVACMode.OFF)

    entity.coordinator.async_stop_climate.assert_awaited_once_with("v1")
    entity.coordinator.async_start_climate.assert_not_called()


async def test_temperature_change_while_on_restarts_with_air_control_on() -> None:
    """The stop/start cycle must bring climate back up, not just stop it.

    This path happened to be safe before the fix, because it can only run
    while climate is already on and the request was seeded True in that case.
    Pinned so the invariant survives future edits.
    """
    entity = _entity(air_control_is_on=True)

    await entity.async_set_temperature(temperature=23)

    _vehicle_id, options = (
        entity.coordinator.async_change_climate_temperature.call_args.args
    )
    assert options.climate is True
    assert options.set_temp == 23
    entity.coordinator.async_stop_climate.assert_not_called()
    entity.coordinator.async_start_climate.assert_not_called()


async def test_turn_on_off_are_supported() -> None:
    """Without these flags climate.turn_on/turn_off reject the entity."""
    entity = _entity(air_control_is_on=False)

    assert entity.supported_features & ClimateEntityFeature.TURN_ON
    assert entity.supported_features & ClimateEntityFeature.TURN_OFF


async def test_turn_on_and_off_route_to_the_car() -> None:
    entity = _entity(air_control_is_on=False)

    await entity.async_turn_on()
    _vehicle_id, options = entity.coordinator.async_start_climate.call_args.args
    assert options.climate is True

    await entity.async_turn_off()
    entity.coordinator.async_stop_climate.assert_awaited_once_with("v1")


async def test_target_temperature_retains_setting_when_air_temp_is_none() -> None:
    """When vehicle air_temperature is None (HVAC off), target_temperature falls back to config."""
    vehicle = Vehicle(id="v1", name="test", model="test")
    vehicle.air_control_is_on = False
    vehicle.air_temperature = (None, "C")
    coordinator = MagicMock(spec=JapanDataUpdateCoordinator)
    coordinator.async_start_climate = AsyncMock()
    coordinator.async_stop_climate = AsyncMock()

    entity = HyundaiBluelinkCarClimateControlSwitch(coordinator, vehicle)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()

    # Initial state is None when car reports off and user hasn't set a temperature
    assert entity.native_target_temperature is None

    # User adjusts temperature via slider while off
    await entity.async_set_temperature(temperature=23.5)
    assert entity.native_target_temperature == 23.5
    coordinator.async_start_climate.assert_not_called()

    # Turning on now sends the user-chosen 23.5 target temp
    await entity.async_turn_on()
    _vehicle_id, options = entity.coordinator.async_start_climate.call_args.args
    assert options.climate is True
    assert options.set_temp == 23.5


async def test_set_temperature_with_hvac_mode_starts_climate() -> None:
    """Calling set_temperature with ATTR_HVAC_MODE starts climate with the given temperature."""
    entity = _entity(air_control_is_on=False)

    await entity.async_set_temperature(temperature=22.5, hvac_mode=HVACMode.AUTO)

    _vehicle_id, options = entity.coordinator.async_start_climate.call_args.args
    assert options.climate is True
    assert options.set_temp == 22.5


async def test_temperature_with_mode_while_running_uses_atomic_restart():
    entity = _entity(air_control_is_on=True)
    await entity.async_set_temperature(temperature=22.5, hvac_mode=HVACMode.AUTO)
    options = entity.coordinator.async_change_climate_temperature.call_args.args[1]
    assert options.set_temp == 22.5
    entity.coordinator.async_start_climate.assert_not_called()
    entity.coordinator.async_stop_climate.assert_not_called()


async def test_coordinator_update_preserves_draft_then_settles_confirmed_target() -> (
    None
):
    entity = _entity(air_control_is_on=False)
    entity.vehicle.air_temperature = (None, "C")
    entity._last_known_temperature = None
    assert entity.native_target_temperature is None

    entity.vehicle.air_temperature = (25.5, "C")
    entity._handle_coordinator_update()
    assert entity.native_target_temperature == 25.5
    entity.vehicle.air_temperature = (None, "C")
    assert entity.native_target_temperature == 25.5

    await entity.async_set_temperature(temperature=26)
    entity.vehicle.air_temperature = (25.5, "C")
    entity._handle_coordinator_update()
    assert entity.native_target_temperature == 26
    await entity.async_turn_on()
    entity._handle_coordinator_update()
    assert entity.native_target_temperature == 26

    entity.vehicle.air_temperature = (26, "C")
    entity._handle_coordinator_update()
    assert entity._requested_temperature is None
    entity.vehicle.air_temperature = (24, "C")
    entity._handle_coordinator_update()
    assert entity.native_target_temperature == 24
    await entity.async_turn_on()
    options = entity.coordinator.async_start_climate.call_args.args[1]
    assert options.set_temp == 24


async def test_start_uses_current_temperature_heaters_and_defrost():
    entity = _entity(air_control_is_on=False)
    entity.vehicle.air_temperature = (25, "C")
    entity.vehicle.defrost_is_on = True
    entity.vehicle.steering_wheel_heater_is_on = True
    entity.vehicle.back_window_heater_is_on = True
    entity._handle_coordinator_update()
    await entity.async_turn_on()
    options = entity.coordinator.async_start_climate.call_args.args[1]
    assert options.set_temp == entity.native_target_temperature == 25
    assert options.heating == 1
    assert options.defrost is True
    assert options.temp_unit == 0


async def test_unknown_temperature_requires_explicit_choice_before_start():
    entity = _entity(air_control_is_on=False)
    entity.vehicle.air_temperature = (None, None)
    entity._last_known_temperature = None
    with pytest.raises(HomeAssistantError, match="Choose a target temperature"):
        await entity.async_turn_on()
    entity.coordinator.async_start_climate.assert_not_called()
    entity.coordinator.async_stop_climate.assert_not_called()


async def test_unsupported_telemetry_setpoint_is_not_silently_changed_on_start():
    entity = _entity(air_control_is_on=False)
    entity.vehicle.air_temperature = (21.25, "C")
    with pytest.raises(HomeAssistantError, match="half-degree"):
        await entity.async_turn_on()
    assert entity.native_target_temperature == 21.25
    entity.coordinator.async_start_climate.assert_not_called()


async def test_unknown_heater_states_are_not_changed_by_start():
    entity = _entity(air_control_is_on=False)
    entity.vehicle.defrost_is_on = None
    entity.vehicle.steering_wheel_heater_is_on = None
    entity.vehicle.back_window_heater_is_on = None
    await entity.async_turn_on()
    options = entity.coordinator.async_start_climate.call_args.args[1]
    assert options.heating is None and options.defrost is None


async def test_unconfirmed_target_expires_without_hiding_external_changes(monkeypatch):
    from custom_components.hyundai_jp import climate

    clock = [1.0]
    monkeypatch.setattr(climate.time, "monotonic", lambda: clock[0])
    entity = _entity(air_control_is_on=False)
    await entity.async_set_temperature(temperature=23)
    await entity.async_turn_on()
    entity.vehicle.air_temperature = (24, "C")
    entity._handle_coordinator_update()
    assert entity.native_target_temperature == 23
    clock[0] = 122
    entity._handle_coordinator_update()
    assert entity.native_target_temperature == 24


async def test_target_expiry_publishes_without_waiting_for_next_poll():
    import asyncio

    entity = _entity(air_control_is_on=False)
    entity._TEMPERATURE_CONFIRMATION_SECONDS = 0.01
    await entity.async_set_temperature(temperature=23)
    await entity.async_turn_on()
    entity.async_write_ha_state.reset_mock()
    await asyncio.sleep(0.03)
    assert entity.native_target_temperature == 21
    entity.async_write_ha_state.assert_called_once()
    assert entity._temperature_expiry_handle is None


async def test_matching_temperature_cancels_the_pending_expiry():
    entity = _entity(air_control_is_on=False)
    await entity.async_set_temperature(temperature=23)
    await entity.async_turn_on()
    handle = entity._temperature_expiry_handle
    assert handle is not None
    entity.vehicle.air_temperature = (23, "C")
    entity._handle_coordinator_update()
    assert handle.cancelled()
    assert entity._temperature_expiry_handle is None


async def test_duplicate_entity_requests_do_not_change_pending_choice():
    entity = _entity(air_control_is_on=False)
    async with entity._request_lock:
        with pytest.raises(HomeAssistantError, match="in progress"):
            await entity.async_set_temperature(temperature=24)
    assert entity.native_target_temperature == 21


async def test_failed_temperature_command_restores_previous_choice():
    entity = _entity(air_control_is_on=True)
    entity.coordinator.async_change_climate_temperature.side_effect = (
        HomeAssistantError("busy")
    )
    with pytest.raises(HomeAssistantError, match="busy"):
        await entity.async_set_temperature(temperature=24)
    assert entity.native_target_temperature == 21


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 100, "21"])
async def test_invalid_temperature_fails_before_any_command(value):
    entity = _entity(air_control_is_on=True)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_temperature(temperature=value)
    entity.coordinator.async_change_climate_temperature.assert_not_called()
    entity.coordinator.async_start_climate.assert_not_called()
    entity.coordinator.async_stop_climate.assert_not_called()


@pytest.mark.parametrize(
    "display_units,requested,expected", [("F", 70, 21), ("C", 21.5, 21.5)]
)
async def test_real_ha_temperature_service_converts_user_input_to_celsius(
    display_units, requested, expected
):
    try:
        from homeassistant.components.climate import async_service_temperature_set
    except ImportError:
        from homeassistant.components.climate.services import (
            _async_service_temperature_set as async_service_temperature_set,
        )
    from homeassistant.core import ServiceCall
    from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

    entity = _entity(air_control_is_on=False)
    entity.vehicle.air_temperature = (None, None)
    entity._last_known_temperature = None
    entity.hass.config.units = (
        US_CUSTOMARY_SYSTEM if display_units == "F" else METRIC_SYSTEM
    )

    await async_service_temperature_set(
        entity,
        ServiceCall(
            entity.hass,
            "climate",
            "set_temperature",
            {"temperature": requested, "hvac_mode": HVACMode.AUTO},
        ),
    )

    options = entity.coordinator.async_start_climate.call_args.args[1]
    assert options.set_temp == entity.native_target_temperature == expected
    assert options.temp_unit == 0
    assert entity.state_attributes["temperature"] == requested
