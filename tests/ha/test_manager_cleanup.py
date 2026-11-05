"""Shared coordinator shutdown closes managers without regional setup changes."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import pytest

from custom_components import hyundai_jp
from custom_components.hyundai_jp.const import DOMAIN
from tests.ha.helpers import make_cleanup_coordinator, make_unload_context


@pytest.fixture
def stop_polling(monkeypatch):
    result = AsyncMock()
    monkeypatch.setattr(DataUpdateCoordinator, "async_shutdown", result)
    return result


async def test_shutdown_waits_for_active_command_and_closes_off_loop(stop_polling):
    events = []
    loop_thread = threading.get_ident()
    close_threads = []
    stopping = asyncio.Event()

    async def polling():
        events.append("polling_stopped")
        stopping.set()

    stop_polling.side_effect = polling

    def close():
        events.append("manager_closed")
        close_threads.append(threading.get_ident())

    result = make_cleanup_coordinator(SimpleNamespace(close=close))
    result.hass.async_add_executor_job.side_effect = asyncio.to_thread
    async with result._action_lock:
        task = asyncio.create_task(result.async_shutdown())
        await stopping.wait()
        assert close_threads == []
        events.append("command_finished")
    await task

    assert events == ["polling_stopped", "command_finished", "manager_closed"]
    assert close_threads[0] != loop_thread


async def test_successful_unload_closes_only_that_manager(monkeypatch, stop_polling):
    close = Mock()
    other_close = Mock()
    result = make_cleanup_coordinator(SimpleNamespace(close=close))
    hass, entry = make_unload_context(result)
    hass.data[DOMAIN]["other-entry"] = make_cleanup_coordinator(
        SimpleNamespace(close=other_close)
    )
    services = Mock()
    monkeypatch.setattr(hyundai_jp, "async_unload_services", services)

    assert await hyundai_jp.async_unload_entry(hass, entry)

    close.assert_called_once()
    other_close.assert_not_called()
    assert list(hass.data[DOMAIN]) == ["other-entry"]
    services.assert_not_called()


async def test_failed_platform_unload_leaves_manager_running(stop_polling):
    close = Mock()
    result = make_cleanup_coordinator(SimpleNamespace(close=close))
    hass, entry = make_unload_context(result, platform_success=False)

    assert not await hyundai_jp.async_unload_entry(hass, entry)

    stop_polling.assert_not_awaited()
    close.assert_not_called()
    assert entry.unique_id in hass.data[DOMAIN]


async def test_cleanup_failure_is_sanitized_and_does_not_block_unload(
    monkeypatch, caplog, stop_polling
):
    result = make_cleanup_coordinator(
        SimpleNamespace(close=Mock(side_effect=RuntimeError("PRIVATE_ACCOUNT_DATA")))
    )
    hass, entry = make_unload_context(result)
    services = Mock()
    monkeypatch.setattr(hyundai_jp, "async_unload_services", services)

    assert await hyundai_jp.async_unload_entry(hass, entry)

    assert hass.data[DOMAIN] == {}
    assert "Unable to shut down vehicle connection (HomeAssistantError)" in caplog.text
    assert "Japan shutdown vehicle manager failed (RuntimeError)" in caplog.text
    assert "PRIVATE_ACCOUNT_DATA" not in caplog.text
    services.assert_called_once_with(hass)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConfigEntryAuthFailed("auth"), ConfigEntryAuthFailed),
        (RuntimeError("offline"), ConfigEntryNotReady),
    ],
)
async def test_failed_first_refresh_cleans_up_and_preserves_auth_error(
    monkeypatch, stop_polling, error, expected
):
    close = Mock()
    result = make_cleanup_coordinator(SimpleNamespace(close=close))
    result.async_config_entry_first_refresh = AsyncMock(side_effect=error)
    monkeypatch.setattr(
        hyundai_jp, "JapanDataUpdateCoordinator", Mock(return_value=result)
    )

    with pytest.raises(expected) as info:
        await hyundai_jp.async_setup_entry(Mock(), Mock())

    close.assert_called_once()
    if expected is ConfigEntryAuthFailed:
        assert info.value is error
    else:
        assert info.value.__cause__ is error


async def test_manager_cleanup_runs_even_when_polling_shutdown_fails(stop_polling):
    error = RuntimeError("polling shutdown")
    stop_polling.side_effect = error
    close = Mock()
    result = make_cleanup_coordinator(SimpleNamespace(close=close))

    with pytest.raises(HomeAssistantError) as info:
        await result.async_shutdown()

    assert str(info.value) == "Japan shutdown polling failed (RuntimeError)"
    assert str(error) not in str(info.value)
    close.assert_called_once()


@pytest.mark.parametrize("mode", ["cached", "force"])
async def test_shutdown_drains_active_poll_and_blocks_fallback(mode, stop_polling):
    started = asyncio.Event()
    release = asyncio.Event()
    events = []
    cached = Mock()
    force = Mock()
    close = Mock(side_effect=lambda: events.append("closed"))
    result = make_cleanup_coordinator(
        SimpleNamespace(
            close=close,
            update_vehicle_with_cached_state=cached,
            force_refresh_vehicle_state=force,
            vehicles={"synthetic": SimpleNamespace(name="test vehicle")},
        )
    )
    read = cached if mode == "cached" else force

    async def execute(target, *args):
        if target is read:
            read(*args)
            events.append("poll_started")
            started.set()
            await release.wait()
            events.append("poll_finished")
            if mode == "force":
                raise RuntimeError("synthetic request failure")
        else:
            return target(*args)

    result.hass.async_add_executor_job.side_effect = execute
    polling = asyncio.create_task(result._async_poll_vehicle("synthetic", mode=mode))
    await started.wait()
    shutdown = asyncio.create_task(result.async_shutdown())
    while not getattr(result, "_shutdown_started", False):
        await asyncio.sleep(0)
    assert not close.called
    release.set()
    if mode == "force":
        with pytest.raises(HomeAssistantError, match="shutting down"):
            await polling
        cached.assert_not_called()
    else:
        await polling
    await shutdown

    assert events == ["poll_started", "poll_finished", "closed"]
    read.assert_called_once_with("synthetic")


@pytest.mark.parametrize("worker_fails", [False, True])
async def test_cancelled_api_caller_keeps_worker_owned_until_shutdown(
    worker_fails, stop_polling
):
    started = asyncio.Event()
    release = asyncio.Event()
    finished = []
    close = Mock()
    result = make_cleanup_coordinator(SimpleNamespace(close=close))
    target = Mock()

    async def execute(job):
        if job is target:
            started.set()
            await release.wait()
            finished.append(True)
            if worker_fails:
                raise RuntimeError("synthetic request failure")
        else:
            job()

    result.hass.async_add_executor_job.side_effect = execute
    caller = asyncio.create_task(result._async_api_job(target))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    shutdown = asyncio.create_task(result.async_shutdown())
    while not getattr(result, "_shutdown_started", False):
        await asyncio.sleep(0)
    assert not close.called
    release.set()
    await shutdown

    assert finished == [True]
    close.assert_called_once()
    assert not result._api_jobs


async def test_no_api_jobs_submitted_after_shutdown(stop_polling):
    close = Mock()
    result = make_cleanup_coordinator(SimpleNamespace(close=close))
    target = Mock()
    await result.async_shutdown()

    with pytest.raises(HomeAssistantError, match="shutting down"):
        await result._async_api_job(target)

    assert result.hass.async_add_executor_job.await_args_list == [
        call(result.vehicle_manager.close),
    ]
    result.vehicle_manager.api.close.assert_called_once()
    target.assert_not_called()


async def test_cancelled_shutdown_still_drains_worker_and_closes(stop_polling):
    started = asyncio.Event()
    release = asyncio.Event()
    close = Mock()
    result = make_cleanup_coordinator(SimpleNamespace(close=close))
    target = Mock()

    async def execute(job):
        if job is target:
            started.set()
            await release.wait()
        else:
            job()

    result.hass.async_add_executor_job.side_effect = execute
    caller = asyncio.create_task(result._async_api_job(target))
    await started.wait()
    shutdown = asyncio.create_task(result.async_shutdown())
    while not getattr(result, "_shutdown_started", False):
        await asyncio.sleep(0)
    shutdown.cancel()
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert not close.called
    release.set()
    await caller
    with pytest.raises(asyncio.CancelledError):
        await shutdown

    close.assert_called_once()
