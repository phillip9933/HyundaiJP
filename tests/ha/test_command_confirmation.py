"""Japan distinguishes service acceptance from physical completion and failures."""

from unittest.mock import AsyncMock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp._api.const import ORDER_STATUS
from tests.ha.helpers import make_operation_coordinator


@pytest.mark.parametrize(
    "status",
    [
        ORDER_STATUS.FAILED,
        ORDER_STATUS.TIMEOUT,
        ORDER_STATUS.UNKNOWN,
        ORDER_STATUS.PENDING,
        None,
    ],
)
async def test_unconfirmed_command_raises_after_refresh(monkeypatch, status):
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.asyncio.sleep", AsyncMock()
    )
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = ["SYNTHETIC_ID", status, None]
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await result.async_lock_vehicle("CAR")
    result.async_refresh.assert_not_awaited()
    result.hass.async_add_executor_job.assert_awaited_with(
        result.vehicle_manager.update_all_vehicles_with_cached_state
    )


async def test_confirmed_command_completes(monkeypatch):
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.asyncio.sleep", AsyncMock()
    )
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = [
        "SYNTHETIC_ID",
        ORDER_STATUS.SUCCESS,
        None,
    ]
    await result.async_lock_vehicle("CAR")
    result.async_refresh.assert_not_awaited()
    result.hass.async_add_executor_job.assert_awaited_with(
        result.vehicle_manager.update_all_vehicles_with_cached_state
    )


async def test_forced_refresh_wait_also_checks_confirmation(monkeypatch):
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.asyncio.sleep", AsyncMock()
    )
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = [ORDER_STATUS.FAILED, None]
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await result.async_await_action_and_refresh("CAR", "SYNTHETIC_ID")
    result.async_set_updated_data.assert_called_once()


@pytest.mark.parametrize(
    "status,reason",
    [
        (ORDER_STATUS.FAILED, "service reported failure"),
        (ORDER_STATUS.TIMEOUT, "matching completion reply"),
        (ORDER_STATUS.PENDING, "still pending"),
    ],
)
async def test_confirmation_error_preserves_specific_outcome(
    monkeypatch, status, reason
):
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.asyncio.sleep", AsyncMock()
    )
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = ["SYNTHETIC_ID", status, None]
    with pytest.raises(HomeAssistantError, match=reason):
        await result.async_unlock_vehicle("CAR")
    assert not result._action_lock.locked()


async def test_http_acceptance_completes_without_waiting_for_mqtt(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.hyundai_jp.coordinator.asyncio.sleep", sleep)
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = [
        "SYNTHETIC_ID",
        ORDER_STATUS.ACCEPTED,
        None,
    ]
    await result.async_lock_vehicle("CAR")
    sleep.assert_not_awaited()
    assert result.hass.async_add_executor_job.await_args_list[1].args == (
        result.vehicle_manager.check_action_status,
        "CAR",
        "SYNTHETIC_ID",
    )
    result.vehicle_manager.force_refresh_vehicle_state.assert_not_called()
    result.async_set_updated_data.assert_called_once()
    assert not result._action_lock.locked()


async def test_accepted_request_with_state_read_failure_is_labelled_accepted():
    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = [
        "SYNTHETIC_ID",
        ORDER_STATUS.ACCEPTED,
        RuntimeError("synthetic failure"),
    ]
    with pytest.raises(HomeAssistantError, match="service accepted the request"):
        await result.async_unlock_vehicle("CAR")
    assert not result._action_lock.locked()


async def test_request_timeout_is_unknown_not_a_reason_to_resend():
    from custom_components.hyundai_jp._api.exceptions import RequestTimeoutError

    result = make_operation_coordinator()
    result.hass.async_add_executor_job.side_effect = RequestTimeoutError(
        "synthetic timeout"
    )
    with pytest.raises(HomeAssistantError, match="vehicle execution is unknown"):
        await result.async_unlock_vehicle("CAR")
    assert result.hass.async_add_executor_job.await_count == 1
    assert not result._action_lock.locked()
