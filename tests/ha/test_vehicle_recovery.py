"""Successful reads recover only their own car and retain account failures."""

import time
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp._api.const import ORDER_STATUS
from custom_components.hyundai_jp._api.exceptions import (
    AuthenticationError,
    RateLimitingError,
    ServiceTemporaryUnavailable,
)
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator
from custom_components.hyundai_jp.entity import HyundaiBluelinkEntity


@pytest.fixture
async def recovery(monkeypatch, tmp_path):
    """Run the real HA coordinator and listeners with only cloud I/O mocked."""
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Synthetic account",
        data={"username": "synthetic", "password": "synthetic", "pin": "0000"},
        options={},
        unique_id="synthetic-account",
        source="user",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    vehicles = {
        key: Vehicle(id=key, name=f"Synthetic {key}") for key in ("a", "b", "c")
    }
    manager = SimpleNamespace(
        vehicles=vehicles,
        token=None,
        get_vehicle=vehicles.__getitem__,
        check_and_refresh_token=Mock(return_value=False),
        update_vehicle_with_cached_state=Mock(),
        force_refresh_vehicle_state=Mock(),
        apply_cached_mqtt_state=Mock(return_value=False),
        close=Mock(),
        enable_vehicle=lambda key: setattr(vehicles[key], "enabled", True),
        disable_vehicle=lambda key: setattr(vehicles[key], "enabled", False),
    )
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.VehicleManager",
        Mock(return_value=manager),
    )
    registry = Mock()
    registry.async_get_device_by_identifier.return_value = None
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.dr.async_get",
        Mock(return_value=registry),
    )
    owner = JapanDataUpdateCoordinator(hass, entry)
    owner.data = None
    owner.last_update_success = True
    changed = Mock()
    remove = owner.async_add_listener(changed)
    yield SimpleNamespace(
        owner=owner,
        manager=manager,
        vehicles=vehicles,
        changed=changed,
        registry=registry,
    )
    remove()
    await owner.async_shutdown()
    await hass.async_block_till_done()


async def _fail_initial_poll(environment):
    manager = environment.manager
    manager.update_vehicle_with_cached_state.side_effect = ServiceTemporaryUnavailable(
        "synthetic"
    )
    await environment.owner._async_poll_vehicles(mode="cached")
    manager.update_vehicle_with_cached_state.side_effect = None
    assert environment.owner._failed_vehicles == {"a", "b", "c"}


async def test_manual_force_recovers_only_the_successful_car(recovery):
    owner, manager = recovery.owner, recovery.manager
    await _fail_initial_poll(recovery)
    before = recovery.changed.call_count
    await owner.async_force_refresh_vehicle("a")
    assert owner.vehicle_available("a") is True
    assert owner._failed_vehicles == {"b", "c"}
    assert "a" not in owner._vehicle_failures
    assert owner._vehicle_failures == {"b": 1, "c": 1}
    manager.force_refresh_vehicle_state.assert_called_once_with("a")
    assert recovery.changed.call_count > before


async def test_failed_manual_force_marks_only_the_read_target_without_retry(recovery):
    owner, manager = recovery.owner, recovery.manager
    manager.force_refresh_vehicle_state.side_effect = ServiceTemporaryUnavailable(
        "synthetic"
    )
    with pytest.raises(ServiceTemporaryUnavailable):
        await owner.async_force_refresh_vehicle("a")
    assert owner._failed_vehicles == {"a"}
    assert owner._vehicle_failures == {"a": 1}
    manager.force_refresh_vehicle_state.assert_called_once_with("a")
    manager.update_vehicle_with_cached_state.assert_not_called()


@pytest.mark.parametrize(
    "error", [AuthenticationError("synthetic"), RateLimitingError("synthetic")]
)
async def test_account_rejection_does_not_clear_vehicle_failure_or_retry(
    recovery, error
):
    owner, manager = recovery.owner, recovery.manager
    owner._failed_vehicles = {"a"}
    owner._vehicle_failures = {"a": 3}
    manager.force_refresh_vehicle_state.side_effect = error
    with pytest.raises(type(error)):
        await owner.async_force_refresh_vehicle("a")
    assert owner._failed_vehicles == {"a"}
    assert owner._vehicle_failures == {"a": 3}
    manager.force_refresh_vehicle_state.assert_called_once_with("a")
    manager.update_vehicle_with_cached_state.assert_not_called()


async def test_command_cached_reads_recover_successes_and_retain_unread_cars(recovery):
    owner, manager = recovery.owner, recovery.manager
    await _fail_initial_poll(recovery)

    def read(vehicle_id):
        if vehicle_id == "b":
            raise ServiceTemporaryUnavailable("synthetic")

    manager.update_vehicle_with_cached_state.reset_mock(side_effect=True)
    manager.update_vehicle_with_cached_state.side_effect = read
    with pytest.raises(HomeAssistantError, match="service accepted the request"):
        await owner._async_refresh_command_state(ORDER_STATUS.ACCEPTED)
    assert [
        call.args for call in manager.update_vehicle_with_cached_state.call_args_list
    ] == [("a",), ("b",)]
    assert owner._failed_vehicles == {"b", "c"}
    assert owner._vehicle_failures == {"b": 2, "c": 1}
    assert owner.vehicle_available("a") is True
    manager.force_refresh_vehicle_state.assert_not_called()


async def _watch_once(environment, *, changed):
    owner, manager = environment.owner, environment.manager
    manager.apply_cached_mqtt_state.return_value = changed
    owner._POST_COMMAND_WATCH_SECONDS = 1
    clock = [0.0]
    owner._post_command_watch_clock = lambda: clock[0]

    async def finish(seconds):
        clock[0] += seconds

    owner._async_wait_for_post_command_poll = finish
    owner._start_post_command_state_watch("a")
    await owner._post_command_watch_tasks["a"]


@pytest.mark.parametrize("changed", [False, True])
async def test_passive_recovery_requires_an_actual_applied_update(recovery, changed):
    owner, manager = recovery.owner, recovery.manager
    await _fail_initial_poll(recovery)
    manager.update_vehicle_with_cached_state.reset_mock()
    await _watch_once(recovery, changed=changed)
    assert owner.vehicle_available("a") is changed
    assert owner._failed_vehicles == ({"b", "c"} if changed else {"a", "b", "c"})
    manager.apply_cached_mqtt_state.assert_called_once_with("a")
    manager.update_vehicle_with_cached_state.assert_not_called()
    manager.force_refresh_vehicle_state.assert_not_called()


@pytest.mark.parametrize("path", ["manual", "passive", "command"])
async def test_vehicle_recovery_never_clears_an_account_error_or_retry_schedule(
    recovery, path
):
    owner = recovery.owner
    owner._failed_vehicles = {"a", "b", "c"}
    owner.last_update_success = False
    owner.last_exception = UpdateFailed("synthetic account limit", retry_after=7200)
    account_error = owner.last_exception
    # A recovery publication must not invoke DUC.async_set_updated_data: that
    # would erase the account error and reschedule its delayed poll.
    owner.async_set_updated_data = Mock(wraps=owner.async_set_updated_data)
    owner._force_refresh_retry_at = {"b": 12345.0}
    owner._force_refresh_failures = {"b": 2}
    if path == "manual":
        await owner.async_force_refresh_vehicle("a")
    elif path == "passive":
        await _watch_once(recovery, changed=True)
    else:
        await owner._async_refresh_command_state(ORDER_STATUS.ACCEPTED)
    owner.async_set_updated_data.assert_not_called()
    assert owner.last_update_success is False
    assert owner.last_exception is account_error
    assert owner._force_refresh_retry_at == {"b": 12345.0}
    assert owner._force_refresh_failures == {"b": 2}
    assert "a" not in owner._failed_vehicles
    assert HyundaiBluelinkEntity(owner, recovery.vehicles["a"]).available is False
    assert recovery.changed.call_count > 0


async def test_disabled_noop_does_not_claim_recovery(recovery):
    owner = recovery.owner
    owner._failed_vehicles = {"a"}
    recovery.vehicles["a"].enabled = False
    recovery.registry.async_get_device_by_identifier.return_value = SimpleNamespace(
        disabled=True
    )
    await owner.async_force_refresh_vehicle("a")
    assert owner.vehicle_available("a") is False


async def test_single_car_poll_failure_then_manual_refresh_restores_real_entity(
    recovery,
):
    owner, manager = recovery.owner, recovery.manager
    del recovery.vehicles["b"]
    del recovery.vehicles["c"]
    # Use the normal periodic cached fallback while a car's wake-up is delayed.
    owner._force_refresh_retry_at["a"] = time.monotonic() + 900
    manager.update_vehicle_with_cached_state.side_effect = ServiceTemporaryUnavailable(
        "synthetic"
    )
    entity = HyundaiBluelinkEntity(owner, recovery.vehicles["a"])
    await owner.async_refresh()
    assert owner.last_update_success is True
    assert entity.available is False
    await owner.async_force_refresh_vehicle("a")
    assert owner.last_update_success is True
    assert entity.available is True
    assert owner._failed_vehicles == set()
    manager.force_refresh_vehicle_state.assert_called_once_with("a")


async def test_poll_abort_retains_unread_failures_and_successful_progress(recovery):
    owner, manager = recovery.owner, recovery.manager
    owner._failed_vehicles = {"a", "c"}
    owner._vehicle_failures = {"a": 2, "c": 1}
    manager.update_vehicle_with_cached_state.side_effect = [
        None,
        RateLimitingError("synthetic"),
    ]
    with pytest.raises(UpdateFailed) as error:
        await owner._async_poll_vehicles(mode="cached")
    assert error.value.retry_after == 7200
    assert owner._failed_vehicles == {"c"}
    assert owner._vehicle_failures == {"c": 1}
    assert [
        call.args for call in manager.update_vehicle_with_cached_state.call_args_list
    ] == [("a",), ("b",)]
