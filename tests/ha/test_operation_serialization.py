"""Japan MQTT commands, token refresh, and status polling share one operation lock."""

import asyncio
from unittest.mock import AsyncMock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp._api.const import ORDER_STATUS
from tests.ha.helpers import make_operation_coordinator


async def test_periodic_poll_skips_busy_japan_without_token_or_network_calls():
    result = make_operation_coordinator()
    result._async_update_data_unlocked = AsyncMock()
    async with result._action_lock:
        assert await result._async_update_data() == {}
    result._async_update_data_unlocked.assert_not_awaited()
    result.async_check_and_refresh_token.assert_not_awaited()


async def test_periodic_poll_holds_lock_during_token_and_status_work():
    result = make_operation_coordinator()

    async def work():
        assert result._action_lock.locked()
        return {}

    result._async_update_data_unlocked = AsyncMock(side_effect=work)
    await result._async_update_data()
    assert not result._action_lock.locked()


@pytest.mark.parametrize(
    "method,args",
    [
        ("async_force_update_all", ()),
        ("async_force_refresh_vehicle", ("synthetic",)),
        ("async_update_all", ()),
        ("async_request_svm_capture", ("synthetic",)),
    ],
)
async def test_manual_operations_reject_busy_before_token_refresh(method, args):
    result = make_operation_coordinator()
    async with result._action_lock:
        with pytest.raises(HomeAssistantError, match="operation is in progress"):
            await getattr(result, method)(*args)
    result.async_check_and_refresh_token.assert_not_awaited()
    result.hass.async_add_executor_job.assert_not_awaited()


async def test_svm_capture_refreshes_token_while_holding_lock():
    result = make_operation_coordinator()

    async def refresh_token():
        assert result._action_lock.locked()

    result.async_check_and_refresh_token.side_effect = refresh_token
    result.hass.async_add_executor_job.return_value = "synthetic-details"
    assert await result.async_request_svm_capture("synthetic") == "synthetic-details"
    result.async_check_and_refresh_token.assert_awaited_once()
    assert not result._action_lock.locked()


async def test_command_poll_and_manual_force_cannot_replace_active_session(monkeypatch):
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.asyncio.sleep", AsyncMock()
    )
    result = make_operation_coordinator()
    started, release = asyncio.Event(), asyncio.Event()

    async def job(fn, *args):
        assert result._action_lock.locked()
        if fn is result.vehicle_manager.check_action_status:
            started.set()
            await release.wait()
            return ORDER_STATUS.SUCCESS
        if fn is result.vehicle_manager.update_all_vehicles_with_cached_state:
            return None
        return "synthetic-action"

    result.hass.async_add_executor_job.side_effect = job
    command = asyncio.create_task(result.async_lock_vehicle("synthetic"))
    await started.wait()
    assert await result._async_update_data() == {}
    with pytest.raises(HomeAssistantError, match="operation is in progress"):
        await result.async_force_refresh_vehicle("synthetic")
    release.set()
    await asyncio.wait_for(command, timeout=1)
    result.async_refresh.assert_not_awaited()
    result.async_check_and_refresh_token.assert_awaited_once()
    assert not result._action_lock.locked()


async def test_confirmed_command_telemetry_failure_is_reported_accurately(monkeypatch):
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.asyncio.sleep", AsyncMock()
    )
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = [
        "synthetic-action",
        ORDER_STATUS.SUCCESS,
        RuntimeError("synthetic private response"),
    ]
    with pytest.raises(
        HomeAssistantError, match="command completed, but updated telemetry"
    ) as caught:
        await result.async_lock_vehicle("synthetic")
    assert "private response" not in str(caught.value)
    assert not result._action_lock.locked()


async def test_unconfirmed_result_remains_unconfirmed_if_refresh_also_fails(
    monkeypatch,
):
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.asyncio.sleep", AsyncMock()
    )
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = [
        "synthetic-action",
        ORDER_STATUS.FAILED,
        RuntimeError("synthetic private response"),
    ]
    with pytest.raises(HomeAssistantError, match="completion was not confirmed"):
        await result.async_lock_vehicle("synthetic")
    assert not result._action_lock.locked()
