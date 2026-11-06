"""A command's cached state follow-up belongs only to its account and car."""

import asyncio
import datetime as dt
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp._api import Token, Vehicle, VehicleManager
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.const import ORDER_STATUS, VEHICLE_LOCK_ACTION
from custom_components.hyundai_jp._api.exceptions import (
    AuthenticationError,
    RateLimitingError,
    ServiceTemporaryUnavailable,
)
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator


@pytest.fixture
async def command_accounts(monkeypatch, tmp_path):
    """Use real HA coordinators/managers, mocking only API protocol boundaries."""
    hass = HomeAssistant(str(tmp_path))
    accounts = {}
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.VehicleManager",
        lambda username, **kwargs: accounts[username].manager,
    )
    registry = Mock()
    registry.async_get_device_by_identifier.return_value = None
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.dr.async_get",
        Mock(return_value=registry),
    )

    def create(account):
        token = Token(
            username=account,
            pin="0000",
            access_token="synthetic",
            valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )
        manager = VehicleManager(account, "synthetic", "0000", token=token)
        manager.api.close()
        api = Mock(spec=HyundaiBluelinkApi)
        api.lock_action.return_value = f"synthetic-{account}-receipt"
        api.check_action_status.return_value = ORDER_STATUS.ACCEPTED
        api.apply_cached_mqtt_state.return_value = False
        manager.api = api
        vehicles = {
            key: Vehicle(id=key, name=f"Synthetic {key}", is_locked=False)
            for key in ("a", "b")
        }
        for vehicle in vehicles.values():
            vehicle.last_updated_at = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
        manager.vehicles = vehicles
        entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain=DOMAIN,
            title="Synthetic account",
            data={
                "username": account,
                "password": "synthetic",
                "pin": "0000",
                "token": token.to_dict(),
            },
            options={},
            unique_id=f"synthetic-{account}",
            source="user",
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        environment = SimpleNamespace(manager=manager, api=api, vehicles=vehicles)
        accounts[account] = environment
        owner = environment.owner = JapanDataUpdateCoordinator(hass, entry)
        owner.data = None
        owner.last_update_success = True
        # Keep the genuine passive watcher alive without advancing time or doing
        # network work. Shutdown cancels and drains this same background task.
        waiting = asyncio.Event()
        owner._async_wait_for_post_command_poll = lambda _: waiting.wait()
        return environment

    yield create
    for account in accounts.values():
        await account.owner.async_shutdown()
    await hass.async_block_till_done()


async def test_accepted_target_is_not_failed_by_an_unrelated_broken_car(
    command_accounts,
):
    account = command_accounts("one")
    owner, api, vehicles = account.owner, account.api, account.vehicles
    owner._failed_vehicles = {"a", "b"}
    owner._vehicle_failures = {"a": 2, "b": 3}

    def cached(_token, vehicle):
        if vehicle.id == "b":
            raise ServiceTemporaryUnavailable("synthetic unrelated failure")
        # An accepted request plus stale cached telemetry is not completion.
        assert vehicle.is_locked is False

    api.update_vehicle_with_cached_state.side_effect = cached
    await owner.async_lock_vehicle("a")
    api.lock_action.assert_called_once_with(
        account.manager.token, vehicles["a"], VEHICLE_LOCK_ACTION.LOCK
    )
    api.update_vehicle_with_cached_state.assert_called_once_with(
        account.manager.token, vehicles["a"]
    )
    api.force_refresh_vehicle_state.assert_not_called()
    api.refresh_access_token.assert_not_called()
    assert owner._failed_vehicles == {"b"}
    assert owner._vehicle_failures == {"b": 3}
    assert vehicles["a"].is_locked is False
    assert owner.get_pending_target("a", "door_lock") is True
    assert "a" in owner._post_command_watch_tasks
    assert "b" not in owner._post_command_watch_tasks
    assert not owner._action_lock.locked()


@pytest.mark.parametrize(
    "error",
    [
        ServiceTemporaryUnavailable("synthetic"),
        AuthenticationError("synthetic"),
        RateLimitingError("synthetic"),
    ],
)
async def test_target_read_failure_preserves_acceptance_and_watcher_without_replay(
    command_accounts, error
):
    account = command_accounts("one")
    owner, api = account.owner, account.api
    api.update_vehicle_with_cached_state.side_effect = error
    with pytest.raises(
        HomeAssistantError, match="service accepted the request"
    ) as raised:
        await owner.async_lock_vehicle("a")
    assert raised.value.action_submitted is True
    assert raised.value.request_rejected is False
    assert raised.value.__cause__ is error
    api.lock_action.assert_called_once()
    api.update_vehicle_with_cached_state.assert_called_once_with(
        account.manager.token, account.vehicles["a"]
    )
    api.force_refresh_vehicle_state.assert_not_called()
    api.refresh_access_token.assert_not_called()
    assert owner.get_pending_target("a", "door_lock") is True
    assert "a" in owner._post_command_watch_tasks
    assert not owner._action_lock.locked()


async def test_rejected_receipt_reads_only_target_and_never_reports_execution_success(
    command_accounts,
):
    account = command_accounts("one")
    owner, api = account.owner, account.api
    api.check_action_status.return_value = ORDER_STATUS.FAILED
    with pytest.raises(HomeAssistantError, match="service reported failure") as raised:
        await owner.async_lock_vehicle("a")
    assert raised.value.request_rejected is True
    api.lock_action.assert_called_once()
    api.update_vehicle_with_cached_state.assert_called_once_with(
        account.manager.token, account.vehicles["a"]
    )
    assert owner.get_pending_target("a", "door_lock") is None
    assert owner.get_pending_status("a", "door_lock") == "rejected"
    assert not owner._post_command_watch_tasks
    assert account.vehicles["a"].is_locked is False


async def test_explicit_account_refresh_still_reads_each_car(command_accounts):
    account = command_accounts("one")
    await account.owner.async_update_all()
    assert [
        call.args[1].id
        for call in account.api.update_vehicle_with_cached_state.call_args_list
    ] == ["a", "b"]
    account.api.force_refresh_vehicle_state.assert_not_called()
    account.api.lock_action.assert_not_called()


async def test_same_vehicle_ids_in_other_account_are_never_read_or_recovered(
    command_accounts,
):
    first, second = command_accounts("one"), command_accounts("two")
    first.owner._failed_vehicles = {"a", "b"}
    second.owner._failed_vehicles = {"a", "b"}
    second.owner._vehicle_failures = {"a": 2, "b": 3}
    second.api.update_vehicle_with_cached_state.side_effect = AssertionError(
        "wrong account"
    )
    await first.owner.async_lock_vehicle("a")
    assert first.owner._failed_vehicles == {"b"}
    assert second.owner._failed_vehicles == {"a", "b"}
    assert second.owner._vehicle_failures == {"a": 2, "b": 3}
    assert not second.api.mock_calls
    assert not second.owner._post_command_watch_tasks
    first.api.update_vehicle_with_cached_state.assert_called_once_with(
        first.manager.token, first.vehicles["a"]
    )
