"""Native entity progress waits for fresh telemetry, not HTTP acceptance."""

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.components.cover import CoverDeviceClass, CoverEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import pytest

from custom_components.hyundai_jp import button, command_state
from custom_components.hyundai_jp._api import Vehicle, VehicleManager
from custom_components.hyundai_jp._api.const import ORDER_STATUS
from custom_components.hyundai_jp._api.exceptions import RequestTimeoutError
from custom_components.hyundai_jp.cover import (
    HyundaiBluelinkChargePortCover,
    async_setup_entry,
)
from custom_components.hyundai_jp.lock import HyundaiBluelinkLock
from custom_components.hyundai_jp.switch import SWITCH_DESCRIPTIONS
from tests.ha.helpers import make_coordinator, make_hass, make_operation_coordinator


@pytest.fixture
async def controls(monkeypatch):
    monkeypatch.setattr(DataUpdateCoordinator, "async_shutdown", AsyncMock())
    vehicle = Vehicle(
        id="synthetic-car", is_locked=False, ev_charge_port_door_is_open=False
    )
    vehicle.last_updated_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    coordinator = make_operation_coordinator()
    manager = Mock(spec=VehicleManager)
    manager.vehicles = {vehicle.id: vehicle}
    manager.get_vehicle.side_effect = manager.vehicles.__getitem__
    manager.api = SimpleNamespace(close=Mock())
    manager.close.side_effect = manager.api.close
    manager.check_action_status.return_value = ORDER_STATUS.ACCEPTED
    manager.apply_cached_mqtt_state.return_value = False
    for command in ("lock", "unlock", "open_charge_port", "close_charge_port"):
        getattr(manager, command).return_value = command
    coordinator.vehicle_manager = manager

    async def execute(operation, *args):
        return operation(*args)

    coordinator.hass.async_add_executor_job = AsyncMock(side_effect=execute)
    coordinator.hass.async_create_background_task = Mock(
        side_effect=lambda coro, name, **kwargs: asyncio.create_task(coro, name=name)
    )
    waiting = asyncio.Event()
    coordinator._async_wait_for_post_command_poll = lambda _: waiting.wait()
    coordinator.async_set_updated_data = Mock(
        side_effect=lambda _: coordinator.async_update_listeners()
    )
    lock = HyundaiBluelinkLock(coordinator, vehicle)
    port = HyundaiBluelinkChargePortCover(coordinator, vehicle)
    states = []
    coordinator._listeners[0] = (lambda: states.append((lock.state, port.state)), None)
    yield coordinator, manager, vehicle, lock, port, states
    await coordinator.async_shutdown()


async def test_lock_keeps_locking_through_old_cache_until_new_matching_telemetry(
    controls,
):
    coordinator, manager, vehicle, lock, _, states = controls
    assert lock.state == "unlocked"
    baseline = vehicle.last_updated_at
    await lock.async_lock()
    assert lock.state == "locking"
    assert vehicle.is_locked is False
    assert lock.extra_state_attributes["last_confirmed_state"] == "unlocked"
    # An accepted service receipt and stale cache cannot finish the transition.
    coordinator.async_update_listeners()
    assert lock.state == "locking"
    vehicle.is_locked = True
    coordinator.async_update_listeners()
    assert lock.state == "locking"  # same timestamp is still the old snapshot
    vehicle.last_updated_at = baseline + dt.timedelta(seconds=1)
    coordinator.async_update_listeners()
    assert lock.state == "locked"
    assert lock.extra_state_attributes["command_status"] == "confirmed"
    assert ("locking", "closed") in states
    manager.lock.assert_called_once_with(vehicle.id)
    manager.force_refresh_vehicle_state.assert_not_called()


async def test_port_has_open_closed_opening_closing_and_stateful_icons(controls):
    coordinator, manager, vehicle, _, port, _ = controls
    baseline = vehicle.last_updated_at
    assert port.state == "closed"
    assert port.icon == "mdi:door-closed"
    assert port.device_class == CoverDeviceClass.DOOR
    assert port.supported_features == CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    await port.async_open_cover()
    assert port.state == "opening"
    assert vehicle.ev_charge_port_door_is_open is False
    vehicle.ev_charge_port_door_is_open = True
    vehicle.last_updated_at = baseline + dt.timedelta(seconds=1)
    coordinator.async_update_listeners()
    assert port.state == "open"
    assert port.icon == "mdi:door-open"
    await port.async_close_cover()
    assert port.state == "closing"
    vehicle.ev_charge_port_door_is_open = False
    vehicle.last_updated_at = baseline + dt.timedelta(seconds=2)
    coordinator.async_update_listeners()
    assert port.state == "closed"
    manager.open_charge_port.assert_called_once()
    manager.close_charge_port.assert_called_once()


async def test_repeated_and_conflicting_clicks_do_not_submit_more_commands(controls):
    _, manager, _, lock, _, _ = controls
    await lock.async_lock()
    with pytest.raises(HomeAssistantError, match="awaiting confirmation"):
        await lock.async_lock()
    with pytest.raises(HomeAssistantError, match="awaiting confirmation"):
        await lock.async_unlock()
    manager.lock.assert_called_once()
    manager.unlock.assert_not_called()


async def test_other_actuator_and_fresh_opposite_state_do_not_cancel_lock_intent(
    controls,
):
    coordinator, _, vehicle, lock, port, _ = controls
    baseline = vehicle.last_updated_at
    await lock.async_lock()
    await port.async_open_cover()
    vehicle.ev_charge_port_door_is_open = True
    vehicle.last_updated_at = baseline + dt.timedelta(seconds=1)
    coordinator.async_update_listeners()
    assert port.state == "open"
    assert lock.state == "locking"
    assert vehicle.is_locked is False


async def test_unknown_vehicle_values_remain_unknown_after_pending_expiry(controls):
    coordinator, _, vehicle, lock, port, _ = controls
    vehicle.is_locked = None
    vehicle.ev_charge_port_door_is_open = None
    coordinator._pending_commands.TIMEOUT_SECONDS = 0.01
    await port.async_open_cover()
    assert port.state == "opening"
    await asyncio.sleep(0.03)
    assert port.state is None
    assert lock.state is None
    assert port.icon == "mdi:door"
    assert lock.icon == "mdi:lock-outline"
    assert port.extra_state_attributes["last_confirmed_state"] is None


async def test_unknown_submission_retains_progress_and_blocks_retry_without_replay(
    controls,
):
    coordinator, manager, _, lock, _, _ = controls
    manager.lock.side_effect = RequestTimeoutError("synthetic timeout")
    with pytest.raises(HomeAssistantError, match="execution is unknown"):
        await lock.async_lock()
    assert lock.state == "locking"
    assert lock.extra_state_attributes["command_status"] == "submission_unknown"
    with pytest.raises(HomeAssistantError, match="awaiting confirmation"):
        await lock.async_lock()
    manager.lock.assert_called_once()
    assert len(coordinator.get_pending_command_diagnostics()) == 1


async def test_failed_receipt_clears_progress_without_claiming_vehicle_state(controls):
    _, manager, vehicle, lock, _, _ = controls
    manager.check_action_status.return_value = ORDER_STATUS.FAILED
    with pytest.raises(HomeAssistantError, match="service reported failure"):
        await lock.async_lock()
    assert lock.state == "unlocked"
    assert vehicle.is_locked is False
    assert lock.extra_state_attributes["command_status"] == "rejected"


async def test_timeout_actively_notifies_without_telemetry(controls):
    coordinator, _, vehicle, lock, _, states = controls
    coordinator._pending_commands.TIMEOUT_SECONDS = 0.01
    await lock.async_lock()
    assert lock.state == "locking"
    await asyncio.sleep(0.03)
    assert lock.state == "unlocked"
    assert lock.extra_state_attributes["command_status"] == "timed_out"
    assert coordinator.get_pending_command_diagnostics() == [
        {"actuator": "door_lock", "state": "timed_out"}
    ]
    assert states[-1] == ("unlocked", "closed")
    assert vehicle.is_locked is False


async def test_shutdown_cancels_pending_timers_and_late_rearming(controls):
    coordinator, manager, vehicle, lock, _, _ = controls
    await lock.async_lock()
    tracker = coordinator._pending_commands
    timer = next(iter(tracker._pending.values())).timer
    await coordinator.async_shutdown()
    assert timer.cancelled()
    assert not tracker._pending
    tracker.submitted(vehicle.id, "door_lock", "late-action")
    assert not tracker._pending
    manager.close.assert_called_once()
    with pytest.raises(HomeAssistantError, match="shutting down"):
        await lock.async_unlock()


async def test_pending_diagnostics_do_not_export_identifiers(controls):
    coordinator, manager, vehicle, lock, _, _ = controls
    manager.lock.return_value = "SYNTHETIC_PRIVATE_ACTION"
    await lock.async_lock()
    diagnostic = coordinator.get_pending_command_diagnostics()
    assert diagnostic[0]["actuator"] == "door_lock"
    assert vehicle.id not in repr(diagnostic)
    assert "SYNTHETIC_PRIVATE_ACTION" not in repr(diagnostic)


async def test_unknown_baseline_rejects_older_sample_within_request_second(monkeypatch):
    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 0, 0, 0, 500000, tzinfo=dt.UTC)

    monkeypatch.setattr(
        command_state, "dt", SimpleNamespace(datetime=FixedDateTime, UTC=dt.UTC)
    )
    vehicle = Vehicle(id="synthetic", is_locked=True)
    tracker = command_state.PendingCommandTracker(Mock())
    try:
        tracker.begin(vehicle, "door_lock", True)
        vehicle.last_updated_at = FixedDateTime(
            2026, 1, 1, 0, 0, 0, 100000, tzinfo=dt.UTC
        )
        tracker.observe({vehicle.id: vehicle})
        assert tracker.target(vehicle.id, "door_lock") is True
        vehicle.last_updated_at = FixedDateTime(
            2026, 1, 1, 0, 0, 0, 700000, tzinfo=dt.UTC
        )
        tracker.observe({vehicle.id: vehicle})
        assert tracker.target(vehicle.id, "door_lock") is None
        assert tracker.phase(vehicle.id, "door_lock") == "confirmed"
    finally:
        tracker.close()


async def test_charge_port_exists_even_when_windows_are_unsupported():
    vehicle = Vehicle(id="synthetic", ev_charge_port_door_is_open=False)
    coordinator = make_coordinator(vehicle)
    hass, entry = make_hass(coordinator)
    added = Mock()
    await async_setup_entry(hass, entry, added)
    assert len(added.call_args.args[0]) == 1
    assert isinstance(added.call_args.args[0][0], HyundaiBluelinkChargePortCover)


def test_port_has_no_duplicate_switch_control():
    assert "ev_charge_port_door_is_open" not in {d.key for d in SWITCH_DESCRIPTIONS}


def make_port_button(coordinator, vehicle):
    description = next(
        item for item in button.BUTTON_DESCRIPTIONS if item.key == "charge_port_toggle"
    )
    return button.HyundaiBluelinkButton(coordinator, description, vehicle)


@pytest.mark.parametrize("is_open", [False, True])
async def test_port_button_sends_one_opposite_action_and_waits_for_status(
    controls, is_open
):
    coordinator, manager, vehicle, _, port, _ = controls
    vehicle.ev_charge_port_door_is_open = is_open
    control = make_port_button(coordinator, vehicle)
    assert control.icon == ("mdi:door-open" if is_open else "mdi:door-closed")
    assert port.entity_category == EntityCategory.CONFIG
    baseline = vehicle.last_updated_at
    await control.async_press()
    assert port.state == ("closing" if is_open else "opening")
    chosen = manager.close_charge_port if is_open else manager.open_charge_port
    other = manager.open_charge_port if is_open else manager.close_charge_port
    chosen.assert_called_once_with(vehicle.id)
    other.assert_not_called()
    # A second press must not submit another request based on stale position.
    with pytest.raises(HomeAssistantError, match="awaiting confirmation"):
        await control.async_press()
    chosen.assert_called_once()
    other.assert_not_called()
    vehicle.ev_charge_port_door_is_open = not is_open
    vehicle.last_updated_at = baseline + dt.timedelta(seconds=1)
    coordinator.async_update_listeners()
    assert port.state == ("closed" if is_open else "open")
    assert control.icon == ("mdi:door-closed" if is_open else "mdi:door-open")
    # After a confirmed update, the next press correctly chooses the other action.
    await control.async_press()
    other.assert_called_once_with(vehicle.id)
    manager.force_refresh_vehicle_state.assert_not_called()


async def test_port_button_does_not_guess_unknown_position_or_send_a_request(controls):
    coordinator, manager, vehicle, _, _, _ = controls
    control = make_port_button(coordinator, vehicle)
    vehicle.ev_charge_port_door_is_open = None
    assert control.icon == "mdi:door"
    with pytest.raises(HomeAssistantError, match="position is unknown"):
        await control.async_press()
    manager.open_charge_port.assert_not_called()
    manager.close_charge_port.assert_not_called()
    coordinator.hass.async_add_executor_job.assert_not_awaited()


async def test_port_button_rejects_busy_account_without_sending(controls):
    coordinator, manager, vehicle, _, _, _ = controls
    control = make_port_button(coordinator, vehicle)
    async with coordinator._action_lock:
        with pytest.raises(HomeAssistantError, match="Another vehicle action"):
            await control.async_press()
    manager.open_charge_port.assert_not_called()
    manager.close_charge_port.assert_not_called()


async def test_port_button_preserves_unknown_submission_and_does_not_replay(controls):
    coordinator, manager, vehicle, _, port, _ = controls
    control = make_port_button(coordinator, vehicle)
    manager.open_charge_port.side_effect = RequestTimeoutError("synthetic timeout")
    with pytest.raises(HomeAssistantError, match="execution is unknown"):
        await control.async_press()
    assert port.state == "opening"
    with pytest.raises(HomeAssistantError, match="awaiting confirmation"):
        await control.async_press()
    manager.open_charge_port.assert_called_once()
    manager.close_charge_port.assert_not_called()


@pytest.mark.parametrize("position", [True, False, None])
async def test_port_button_setup_follows_reported_port_support(position):
    vehicle = Vehicle(id="synthetic", ev_charge_port_door_is_open=position)
    coordinator = make_coordinator(vehicle)
    hass, entry = make_hass(coordinator)
    created = []
    await button.async_setup_entry(hass, entry, created.extend)
    controls = [e for e in created if e.entity_description.key == "charge_port_toggle"]
    assert len(controls) == (0 if position is None else 1)
    if controls:
        assert controls[0].unique_id == "hyundai_jp_synthetic_charge_port_toggle"
        assert controls[0].entity_registry_enabled_default is True
        assert controls[0].entity_category is None
        assert controls[0].translation_key == "charge_port_toggle"
    refresh = next(e for e in created if e.entity_description.key == "force_refresh")
    assert refresh.icon == "mdi:refresh"
