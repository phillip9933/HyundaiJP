"""Public HA services validate and route commands through the selected account."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp import services
from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator
from custom_components.hyundai_jp.ha_validation import validation


@pytest.fixture
async def environment(monkeypatch, tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entries, accounts = {}, {}
    for name in ("first", "chosen"):
        entry = SimpleNamespace(
            entry_id=f"entry-{name}",
            unique_id=f"uid-{name}",
            state=ConfigEntryState.LOADED,
            domain=DOMAIN,
        )
        coordinator = MagicMock(spec=JapanDataUpdateCoordinator)
        coordinator.config_entry = entry
        coordinator.vehicle_manager = SimpleNamespace(vehicles={name: Vehicle(id=name)})
        entry.runtime_data = coordinator
        entries[entry.entry_id] = entry
        accounts[name] = coordinator
    hass.config_entries = SimpleNamespace(
        async_entries=lambda domain: list(entries.values()),
        async_get_entry=entries.get,
    )
    device = SimpleNamespace(
        config_entries={"entry-chosen"}, identifiers={(DOMAIN, "chosen")}
    )
    registry = SimpleNamespace(
        async_get=lambda device_id: device if device_id == "chosen-device" else None
    )
    monkeypatch.setattr(services.device_registry, "async_get", lambda _: registry)
    services.async_setup_services(hass)
    yield SimpleNamespace(
        hass=hass, chosen=accounts["chosen"], other=accounts["first"], entries=entries
    )
    await hass.async_stop()


async def invoke(environment, service, **data):
    await environment.hass.services.async_call(
        DOMAIN, service, {"device_id": "chosen-device", **data}, blocking=True
    )


@pytest.mark.parametrize(
    "service,method",
    [
        ("lock", "async_lock_vehicle"),
        ("unlock", "async_unlock_vehicle"),
        ("start_charge", "async_start_charge"),
        ("stop_charge", "async_stop_charge"),
        ("open_charge_port", "async_open_charge_port"),
        ("close_charge_port", "async_close_charge_port"),
        ("stop_climate", "async_stop_climate"),
        ("start_hazard_lights", "async_start_hazard_lights"),
        ("start_hazard_lights_and_horn", "async_start_hazard_lights_and_horn"),
        ("start_valet_mode", "async_start_valet_mode"),
        ("stop_valet_mode", "async_stop_valet_mode"),
    ],
)
async def test_selected_control_does_not_cross_accounts_or_replay_after_rejection(
    environment, service, method
):
    e = environment
    action = getattr(e.chosen, method)
    await invoke(e, service)
    action.assert_awaited_once_with("chosen")
    assert e.other.mock_calls == []

    error = HomeAssistantError("Vehicle is busy")
    action.side_effect = error
    with pytest.raises(HomeAssistantError) as caught:
        await invoke(e, service)
    assert caught.value is error
    assert action.await_count == 2
    assert e.other.mock_calls == []


async def test_registered_climate_preserves_explicit_zero_false_and_omitted_choices(
    environment,
):
    e = environment
    await invoke(
        e,
        "start_climate",
        duration=4,
        temperature="23.5",
        climate=False,
        heating=0,
        defrost=False,
        flseat=0,
        frseat=0,
        rlseat=0,
        rrseat=0,
        steering_wheel=0,
    )
    vehicle_id, first = e.chosen.async_start_climate.await_args.args
    assert vehicle_id == "chosen"
    assert first.duration == 4 and first.set_temp == 23.5
    assert first.climate is False and first.defrost is False
    assert first.heating == first.steering_wheel == 0
    assert (
        first.front_left_seat
        == first.front_right_seat
        == first.rear_left_seat
        == first.rear_right_seat
        == 0
    )

    await invoke(e, "start_climate", temperature=22)
    _, second = e.chosen.async_start_climate.await_args.args
    assert second is not first
    assert second.set_temp == 22
    assert second.duration is second.climate is second.defrost is second.heating is None
    assert second.front_left_seat is second.steering_wheel is None
    assert e.other.mock_calls == []


@pytest.mark.parametrize(
    "service,data,method,args",
    [
        (
            "set_charge_limits",
            {"ac_limit": "70", "dc_limit": 80},
            "async_set_charge_limits",
            (70, 80),
        ),
        ("set_charging_current", {"level": "2"}, "async_set_charging_current", (2,)),
    ],
)
async def test_registered_charge_controls_keep_validated_numeric_values(
    environment, service, data, method, args
):
    e = environment
    await invoke(e, service, **data)
    getattr(e.chosen, method).assert_awaited_once_with("chosen", *args)
    assert e.other.mock_calls == []


async def test_capture_service_checks_capability_before_requesting_one_capture(
    environment,
):
    e = environment
    e.chosen.async_supports_svm.return_value = False
    with pytest.raises(HomeAssistantError, match="not available"):
        await invoke(e, "capture_svm_image")
    e.chosen.async_request_svm_capture.assert_not_awaited()
    e.chosen.async_supports_svm.assert_awaited_once_with("chosen")

    e.chosen.async_supports_svm.return_value = True
    await invoke(e, "capture_svm_image")
    e.chosen.async_request_svm_capture.assert_awaited_once_with("chosen")
    assert e.other.mock_calls == []


@pytest.mark.parametrize(
    "service,data",
    [
        ("lock", {"device_id": None}),
        ("unlock", {"unknown_option": True}),
        ("start_climate", {"duration": 4.5}),
        ("start_climate", {"flseat": True}),
        ("set_charging_current", {"level": 0}),
        ("set_charge_limits", {"ac_limit": 80.5, "dc_limit": 80}),
    ],
)
async def test_registered_invalid_calls_fail_before_either_account_is_touched(
    environment, service, data
):
    e = environment
    with pytest.raises(validation.Invalid):
        await invoke(e, service, **data)
    assert e.chosen.mock_calls == e.other.mock_calls == []


async def test_registered_action_cannot_target_an_account_after_it_unloads(environment):
    e = environment
    e.entries["entry-chosen"].state = ConfigEntryState.NOT_LOADED
    with pytest.raises(HomeAssistantError, match="no active"):
        await invoke(e, "unlock")
    assert e.chosen.mock_calls == e.other.mock_calls == []
