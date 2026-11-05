"""Climate stop/wait/start is one prevalidated and cancellation-safe operation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import pytest

from custom_components.hyundai_jp._api import (
    ClimateRequestOptions,
    Vehicle,
    VehicleManager,
)
from custom_components.hyundai_jp._api.exceptions import APIError
from custom_components.hyundai_jp.climate import _submission_is_uncertain
from custom_components.hyundai_jp.coordinator import _ConfirmedCommandRefreshError
from tests.ha.helpers import make_runtime_coordinator


def operation():
    owner = make_runtime_coordinator()
    manager = VehicleManager("synthetic", "synthetic", "0000")
    vehicle = Vehicle(id="synthetic", air_control_is_on=True)
    vehicle.air_temperature = (21, "C")
    vehicle.supports.climate_temperature_range = (17, 27)
    manager.vehicles = {vehicle.id: vehicle}
    manager.api._vehicle_profiles[vehicle.id] = {"option": {"hvacTempType": 1}}
    manager.api.session.request = Mock(side_effect=AssertionError("No network"))
    owner.vehicle_manager = manager
    events = []

    def command(token, current_vehicle, endpoint, payload, **kwargs):
        assert owner._action_lock.locked()
        events.append(payload.copy())
        return "synthetic-action"

    manager.api._japan_command = Mock(side_effect=command)
    manager.get_token = Mock(return_value=object())

    async def execute(target, *args):
        return target(*args)

    owner.hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=execute))
    owner.async_check_and_refresh_token = AsyncMock()
    owner.async_await_action_and_refresh = AsyncMock()
    owner._async_wait_for_climate_restart = AsyncMock()
    return owner, manager, events


@pytest.mark.parametrize("invalid", ["profile", "setpoint"])
async def test_restart_preflight_rejects_invalid_request_before_any_command(invalid):
    owner, manager, events = operation()
    options = ClimateRequestOptions(set_temp=23, climate=True)
    if invalid == "profile":
        manager.api._vehicle_profiles.clear()
    else:
        options.set_temp = None
        manager.get_vehicle("synthetic").air_temperature = (None, "C")
    try:
        with pytest.raises(
            HomeAssistantError, match="Cannot change climate temperature"
        ):
            await owner.async_change_climate_temperature("synthetic", options)
        assert events == []
        owner._async_wait_for_climate_restart.assert_not_awaited()
        assert not owner._action_lock.locked()
        manager.api.session.request.assert_not_called()
    finally:
        manager.close()


async def test_restart_owns_entire_gap_and_snapshots_request_options():
    owner, manager, events = operation()
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait():
        assert owner._action_lock.locked()
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=3)

    owner._async_wait_for_climate_restart.side_effect = wait
    options = ClimateRequestOptions(set_temp=23, climate=True)
    task = asyncio.create_task(
        owner.async_change_climate_temperature("synthetic", options)
    )
    await asyncio.wait_for(entered.wait(), timeout=3)
    assert events == [{"command": "stop"}]
    options.set_temp = 99
    with pytest.raises(HomeAssistantError, match="in progress"):
        await owner.async_lock_vehicle("synthetic")
    release.set()
    await task
    assert events == [
        {"command": "stop"},
        {"command": "start", "hvacTemp": "23.0", "hvacTempType": 1, "tempUnit": "C"},
    ]
    assert not owner._action_lock.locked()
    manager.close()


async def test_cancelled_caller_keeps_ownership_through_restart_worker():
    owner, manager, events = operation()
    paused, resume = asyncio.Event(), asyncio.Event()
    worker_started, worker_finish = asyncio.Event(), asyncio.Event()

    async def wait():
        paused.set()
        await asyncio.wait_for(resume.wait(), timeout=3)

    async def execute(target, *args):
        if getattr(target, "__name__", None) == "<lambda>" and len(events) == 1:
            worker_started.set()
            await asyncio.wait_for(worker_finish.wait(), timeout=3)
        return target(*args)

    owner._async_wait_for_climate_restart.side_effect = wait
    owner.hass.async_add_executor_job.side_effect = execute
    task = asyncio.create_task(
        owner.async_change_climate_temperature(
            "synthetic", ClimateRequestOptions(set_temp=23)
        )
    )
    await asyncio.wait_for(paused.wait(), timeout=3)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert owner._action_lock.locked()
    resume.set()
    await asyncio.wait_for(worker_started.wait(), timeout=3)
    assert not task.done()
    with pytest.raises(HomeAssistantError, match="in progress"):
        await owner.async_stop_climate("synthetic")
    worker_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [event["command"] for event in events] == ["stop", "start"]
    assert not owner._action_lock.locked()
    assert not owner._api_jobs
    manager.close()


@pytest.mark.parametrize("failed_command", ["stop", "start"])
async def test_restart_does_not_replay_after_a_command_failure(failed_command):
    owner, manager, events = operation()
    original = manager.api._japan_command.side_effect

    def command(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[3]["command"] == failed_command:
            raise APIError("synthetic rejection")
        return result

    manager.api._japan_command.side_effect = command
    try:
        with pytest.raises(HomeAssistantError) as caught:
            await owner.async_change_climate_temperature(
                "synthetic", ClimateRequestOptions(set_temp=23)
            )
        assert [event["command"] for event in events] == (
            ["stop"] if failed_command == "stop" else ["stop", "start"]
        )
        if failed_command == "start":
            assert "stop request was accepted" in str(caught.value)
        assert not owner._action_lock.locked()
    finally:
        manager.close()


async def test_shutdown_between_commands_prevents_late_restart(monkeypatch):
    monkeypatch.setattr(DataUpdateCoordinator, "async_shutdown", AsyncMock())
    owner, manager, events = operation()
    paused, resume = asyncio.Event(), asyncio.Event()
    real_close = manager.close
    manager.close = Mock(side_effect=real_close)

    async def wait():
        paused.set()
        await asyncio.wait_for(resume.wait(), timeout=3)

    owner._async_wait_for_climate_restart.side_effect = wait
    task = asyncio.create_task(
        owner.async_change_climate_temperature(
            "synthetic", ClimateRequestOptions(set_temp=23)
        )
    )
    await asyncio.wait_for(paused.wait(), timeout=3)
    shutdown = asyncio.create_task(owner.async_shutdown())
    while not owner._closing:
        await asyncio.sleep(0)
    assert owner._action_lock.locked()
    manager.close.assert_not_called()
    resume.set()
    with pytest.raises(HomeAssistantError, match="restart outcome"):
        await task
    await shutdown
    assert events == [{"command": "stop"}]
    manager.close.assert_called_once()
    assert not owner._api_jobs
    assert not owner._action_lock.locked()


async def test_accepted_stop_with_failed_telemetry_reports_unsent_restart():
    owner, manager, events = operation()
    owner.async_await_action_and_refresh.side_effect = _ConfirmedCommandRefreshError(
        "accepted but stale"
    )
    try:
        with pytest.raises(HomeAssistantError, match="Restart was not sent") as caught:
            await owner.async_change_climate_temperature(
                "synthetic", ClimateRequestOptions(set_temp=23)
            )
        assert events == [{"command": "stop"}]
        assert not _submission_is_uncertain(caught.value)
        owner._async_wait_for_climate_restart.assert_not_awaited()
        assert not owner._action_lock.locked()
    finally:
        manager.close()
