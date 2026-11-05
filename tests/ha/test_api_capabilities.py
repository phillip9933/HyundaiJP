"""Japan command filtering must not alter established regional platforms."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp import button, climate, lock, number, switch
from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.japan_coordinator import JapanDataUpdateCoordinator
from tests.ha.conftest import make_coordinator, make_hass


async def test_japan_rejects_unsupported_service_before_authentication():
    result = JapanDataUpdateCoordinator.__new__(JapanDataUpdateCoordinator)
    result.vehicle_manager = MagicMock()
    result.vehicle_manager.api = SimpleNamespace(
        supported_commands=frozenset({"lock_action"})
    )
    result.vehicle_manager.supported_commands = (
        result.vehicle_manager.api.supported_commands
    )
    result.async_check_and_refresh_token = AsyncMock()
    result.hass = MagicMock()
    result.hass.async_add_executor_job = AsyncMock()
    with pytest.raises(
        HomeAssistantError, match="does not support this vehicle command"
    ):
        await result.async_start_charge("synthetic")
    result.async_check_and_refresh_token.assert_not_awaited()
    result.hass.async_add_executor_job.assert_not_awaited()


async def test_japan_exposes_only_supported_commands():
    vehicle = Vehicle(id="synthetic", name="synthetic", model="synthetic")
    vehicle.air_control_is_on = False
    vehicle.ev_battery_is_charging = False
    vehicle.ev_charge_limits_ac = 80
    vehicle.ev_charge_limits_dc = 90
    vehicle.supports.svm = True
    coordinator = make_coordinator(vehicle)
    coordinator.__class__ = JapanDataUpdateCoordinator
    allowed = {
        "lock_action",
        "start_charge",
        "stop_charge",
        "set_charge_limits",
        "start_hazard_lights",
    }
    coordinator.supports_command = lambda command: command in allowed
    hass, entry = make_hass(coordinator)
    entities = {}
    for platform in (lock, climate, number, switch, button):
        created = []
        await platform.async_setup_entry(
            hass, entry, lambda values, *args, target=created: target.extend(values)
        )
        entities[platform.__name__.rsplit(".", 1)[-1]] = created
    assert len(entities["lock"]) == 1
    assert entities["climate"] == []
    assert {e.entity_description.key for e in entities["number"]} == {
        "ev_charge_limits_ac",
        "ev_charge_limits_dc",
    }
    assert {e.entity_description.key for e in entities["button"]} == {
        "force_refresh",
        "start_hazard_lights",
    }
    assert any(isinstance(e, switch.SVMDewarpSwitch) for e in entities["switch"])
