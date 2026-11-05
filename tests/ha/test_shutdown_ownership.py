"""One owner drains shutdown even when callers overlap, cancel or fail."""

import asyncio
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import pytest

from custom_components import hyundai_jp
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator
from tests.ha.test_manager_cleanup import coordinator


@pytest.mark.parametrize("polling_fails", [False, True])
async def test_overlapping_and_repeated_shutdown_share_one_result(
    monkeypatch, polling_fails
):
    entered, release = asyncio.Event(), asyncio.Event()
    error = RuntimeError("PRIVATE_POLLING_DETAILS")

    async def stop_polling():
        entered.set()
        await release.wait()
        if polling_fails:
            raise error

    polling = AsyncMock(side_effect=stop_polling)
    monkeypatch.setattr(DataUpdateCoordinator, "async_shutdown", polling)
    close = Mock()
    owner = coordinator(SimpleNamespace(close=close))
    watchers = AsyncMock()
    owner._async_stop_post_command_state_watchers = watchers
    first = asyncio.create_task(owner.async_shutdown())
    await entered.wait()
    shared = owner._shutdown_task
    second = asyncio.create_task(owner.async_shutdown())
    await asyncio.sleep(0)
    assert owner._shutdown_task is shared
    assert not first.done() and not second.done()
    close.assert_not_called()
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    if polling_fails:
        assert all(isinstance(outcome, HomeAssistantError) for outcome in outcomes)
        assert outcomes[0] is outcomes[1]
        assert str(outcomes[0]) == "Japan shutdown polling failed (RuntimeError)"
        with pytest.raises(HomeAssistantError) as caught:
            await owner.async_shutdown()
        assert caught.value is outcomes[0]
        assert "PRIVATE_" not in str(caught.value)
    else:
        assert outcomes == [None, None]
        await owner.async_shutdown()
    polling.assert_awaited_once()
    watchers.assert_awaited_once()
    close.assert_called_once()
    owner.vehicle_manager.api.close.assert_called_once()
    assert owner._shutdown_task is shared


@pytest.mark.parametrize("worker_fails", [False, True])
async def test_cancelling_one_shutdown_waiter_keeps_worker_and_shared_owner(
    monkeypatch, worker_fails
):
    monkeypatch.setattr(DataUpdateCoordinator, "async_shutdown", AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()
    close = Mock()
    owner = coordinator(SimpleNamespace(close=close))
    target = Mock()

    async def execute(operation):
        if operation is target:
            entered.set()
            await release.wait()
            if worker_fails:
                raise RuntimeError("PRIVATE_WORKER_DETAILS")
        else:
            operation()

    owner.hass.async_add_executor_job.side_effect = execute
    worker = asyncio.create_task(owner._async_api_job(target))
    await entered.wait()
    first = asyncio.create_task(owner.async_shutdown())
    second = asyncio.create_task(owner.async_shutdown())
    while not getattr(owner, "_shutdown_started", False):
        await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)
    assert not first.done() and not second.done()
    assert owner._api_jobs and all(not task.cancelled() for task in owner._api_jobs)
    close.assert_not_called()
    release.set()
    if worker_fails:
        with pytest.raises(RuntimeError):
            await worker
    else:
        await worker
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert owner._shutdown_task.done()
    close.assert_called_once()
    owner.vehicle_manager.api.close.assert_called_once()
    assert not owner._api_jobs


async def test_polling_failure_still_cancels_watchers_before_closing_manager(
    monkeypatch, caplog
):
    error = RuntimeError("PRIVATE_POLLING_DETAILS")
    monkeypatch.setattr(
        DataUpdateCoordinator, "async_shutdown", AsyncMock(side_effect=error)
    )
    entered, stopped = asyncio.Event(), asyncio.Event()
    events = []

    async def watch():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("watcher_stopped")
            stopped.set()

    close = Mock(side_effect=lambda: events.append("manager_closed"))
    owner = coordinator(SimpleNamespace(close=close))
    watcher = asyncio.create_task(watch())
    owner._post_command_watch_tasks = {"synthetic": watcher}
    await entered.wait()
    with pytest.raises(HomeAssistantError) as caught:
        await owner.async_shutdown()
    assert str(caught.value) == "Japan shutdown polling failed (RuntimeError)"
    assert "PRIVATE_" not in str(caught.value)
    assert watcher.done() and stopped.is_set()
    assert events == ["watcher_stopped", "manager_closed"]
    close.assert_called_once()
    owner.vehicle_manager.api.close.assert_called_once()
    assert "Japan shutdown polling failed (RuntimeError)" in caplog.text
    assert "PRIVATE_POLLING_DETAILS" not in caplog.text


@pytest.mark.parametrize("stage", ["watchers", "manager", "manager_and_api"])
async def test_cleanup_failure_attempts_remaining_stages_with_redacted_logs(
    monkeypatch, caplog, stage
):
    polling = AsyncMock()
    monkeypatch.setattr(DataUpdateCoordinator, "async_shutdown", polling)
    error = RuntimeError("PRIVATE_FIRST_FAILURE")
    close = Mock(side_effect=error if stage.startswith("manager") else None)
    owner = coordinator(SimpleNamespace(close=close))
    watchers = AsyncMock(side_effect=error if stage == "watchers" else None)
    owner._async_stop_post_command_state_watchers = watchers
    if stage == "manager_and_api":
        owner.vehicle_manager.api.close.side_effect = ValueError("PRIVATE_API_FAILURE")
    with pytest.raises(HomeAssistantError) as caught:
        await owner.async_shutdown()
    expected_stage = "state watchers" if stage == "watchers" else "vehicle manager"
    assert str(caught.value) == f"Japan shutdown {expected_stage} failed (RuntimeError)"
    assert "PRIVATE_" not in str(caught.value)
    polling.assert_awaited_once()
    watchers.assert_awaited_once()
    close.assert_called_once()
    owner.vehicle_manager.api.close.assert_called_once()
    assert "PRIVATE_" not in caplog.text
    assert "failed (RuntimeError)" in caplog.text
    if stage == "manager_and_api":
        assert "Japan shutdown vehicle API failed (ValueError)" in caplog.text
    with pytest.raises(HomeAssistantError) as repeated:
        await owner.async_shutdown()
    assert repeated.value is caught.value
    close.assert_called_once()
    owner.vehicle_manager.api.close.assert_called_once()


async def test_explicit_unload_and_real_ha_unload_callback_close_once(
    monkeypatch, tmp_path
):
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Synthetic Japan account",
        data={"username": "synthetic", "password": "synthetic", "pin": "0000"},
        options={},
        unique_id="synthetic-account",
        source="user",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
        state=ConfigEntryState.LOADED,
    )
    adapter_close = Mock()
    manager = SimpleNamespace(api=SimpleNamespace(close=adapter_close))
    manager.close = Mock(side_effect=adapter_close)
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.VehicleManager",
        Mock(return_value=manager),
    )

    async def execute(target, *args):
        return target(*args)

    monkeypatch.setattr(hass, "async_add_executor_job", AsyncMock(side_effect=execute))
    owner = JapanDataUpdateCoordinator(hass, entry)
    assert owner.config_entry is entry
    assert owner.async_shutdown in entry._on_unload
    hass.data[DOMAIN] = {entry.unique_id: owner}
    hass.config_entries = SimpleNamespace(
        async_unload_platforms=AsyncMock(return_value=True)
    )
    services = Mock()
    monkeypatch.setattr(hyundai_jp, "async_unload_services", services)
    assert await hyundai_jp.async_unload_entry(hass, entry)
    shared = owner._shutdown_task
    assert owner._shutdown_requested
    manager.close.assert_called_once()
    adapter_close.assert_called_once()
    await entry._async_process_on_unload(hass)
    await owner.async_shutdown()
    assert owner._shutdown_task is shared and shared.done()
    manager.close.assert_called_once()
    adapter_close.assert_called_once()
    services.assert_called_once_with(hass)
    assert hass.data[DOMAIN] == {}
    assert not entry._on_unload


async def test_real_ha_unload_callback_logs_only_sanitized_shared_failure(
    monkeypatch, tmp_path, caplog
):
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Synthetic Japan account",
        data={"username": "synthetic", "password": "synthetic", "pin": "0000"},
        options={},
        unique_id="synthetic-account",
        source="user",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
        state=ConfigEntryState.LOADED,
    )
    adapter_close = Mock()
    manager = SimpleNamespace(api=SimpleNamespace(close=adapter_close))
    manager.close = Mock(side_effect=RuntimeError("PRIVATE_VENDOR_ACCOUNT_DATA"))
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.VehicleManager",
        Mock(return_value=manager),
    )

    async def execute(target, *args):
        return target(*args)

    monkeypatch.setattr(hass, "async_add_executor_job", AsyncMock(side_effect=execute))
    owner = JapanDataUpdateCoordinator(hass, entry)
    hass.data[DOMAIN] = {entry.unique_id: owner}
    hass.config_entries = SimpleNamespace(
        async_unload_platforms=AsyncMock(return_value=True)
    )
    monkeypatch.setattr(hyundai_jp, "async_unload_services", Mock())
    assert await hyundai_jp.async_unload_entry(hass, entry)
    shared = owner._shutdown_task
    manager.close.assert_called_once()
    adapter_close.assert_called_once()
    await entry._async_process_on_unload(hass)
    await hass.async_block_till_done()
    assert owner._shutdown_task is shared and shared.done()
    assert isinstance(shared.exception(), HomeAssistantError)
    assert (
        str(shared.exception())
        == "Japan shutdown vehicle manager failed (RuntimeError)"
    )
    assert "PRIVATE_VENDOR_ACCOUNT_DATA" not in caplog.text
    assert "Japan shutdown vehicle manager failed (RuntimeError)" in caplog.text
    assert any(
        record.exc_info and isinstance(record.exc_info[1], HomeAssistantError)
        for record in caplog.records
    ), "The actual HA task logger must have processed the callback failure"
    manager.close.assert_called_once()
    adapter_close.assert_called_once()
