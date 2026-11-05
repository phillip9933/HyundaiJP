"""Read recovery uses real manager token rules and never repeats car commands."""

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import DATA_REGISTRY
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
import requests

from custom_components.hyundai_jp._api import Token, Vehicle, VehicleManager
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.exceptions import (
    AuthenticationError,
    RateLimitingError,
    ServiceTemporaryUnavailable,
)
from tests.ha.helpers import make_runtime_coordinator


@pytest.fixture
def account():
    token = Token(
        username="synthetic",
        password="synthetic",
        access_token="rejected",
        refresh_token="refresh",
        mqtt_registration_uuid="phone",
        device_id="device",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    manager = VehicleManager("synthetic", "synthetic", "0000", token=token)
    manager.api.close()
    manager.api = Mock(spec=HyundaiBluelinkApi)
    manager.vehicles = {name: Vehicle(id=name) for name in ("a", "b")}
    coordinator = make_runtime_coordinator()
    coordinator.vehicle_manager = manager
    coordinator.config_entry = SimpleNamespace(
        entry_id="synthetic-entry", data={"token": token.to_dict()}
    )

    async def execute(operation, *args):
        return operation(*args)

    coordinator.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=execute),
        config_entries=SimpleNamespace(async_update_entry=Mock()),
        data={
            DATA_REGISTRY: SimpleNamespace(
                async_get_device_by_identifier=lambda *args: None
            )
        },
    )
    coordinator.force_refresh_interval = 1440
    coordinator.data = None
    return coordinator, manager


def _renewed(token):
    return Token(
        username=token.username,
        password="synthetic",
        access_token="renewed",
        refresh_token=token.refresh_token,
        device_id=token.device_id,
        mqtt_registration_uuid=token.mqtt_registration_uuid,
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )


async def test_rejected_future_token_recovers_real_manager_and_saves_once(account):
    coordinator, manager = account
    manager.api.refresh_access_token.side_effect = _renewed

    def read(token, vehicle):
        if token.access_token == "rejected":
            raise AuthenticationError("synthetic rejection")
        vehicle.is_locked = True

    manager.api.update_vehicle_with_cached_state.side_effect = read
    await coordinator._async_poll_vehicles(mode="cached")

    assert all(vehicle.is_locked for vehicle in manager.vehicles.values())
    assert manager.token.access_token == "renewed"
    assert manager.token.refresh_token == "refresh"
    assert manager.token.mqtt_registration_uuid == "phone"
    manager.api.refresh_access_token.assert_called_once()
    assert manager.api.update_vehicle_with_cached_state.call_count == 3
    coordinator.hass.config_entries.async_update_entry.assert_called_once()
    manager.api.force_refresh_vehicle_state.assert_not_called()
    assert not coordinator._failed_vehicles


async def test_two_rejected_cars_refresh_only_once_and_do_not_loop(account):
    coordinator, manager = account
    manager.api.refresh_access_token.side_effect = _renewed
    manager.api.update_vehicle_with_cached_state.side_effect = AuthenticationError(
        "rejected"
    )
    with pytest.raises(UpdateFailed, match="after renewing"):
        await coordinator._async_poll_vehicles(mode="cached")
    manager.api.refresh_access_token.assert_called_once()
    assert manager.api.update_vehicle_with_cached_state.call_count == 3
    assert coordinator._failed_vehicles == {"a", "b"}
    assert manager.token.refresh_token == "refresh"


async def test_failed_refresh_grant_prompts_reauthentication(account):
    coordinator, manager = account
    manager.api.update_vehicle_with_cached_state.side_effect = AuthenticationError(
        "rejected"
    )
    manager.api.refresh_access_token.side_effect = AuthenticationError("grant rejected")
    with pytest.raises(ConfigEntryAuthFailed, match="could not be renewed"):
        await coordinator._async_poll_vehicles(mode="cached")
    manager.api.refresh_access_token.assert_called_once()
    assert manager.api.update_vehicle_with_cached_state.call_count == 1


async def test_rejected_forced_refresh_is_not_repeated_or_reported_as_cached_success(
    account,
):
    coordinator, manager = account
    manager.api.force_refresh_vehicle_state.side_effect = AuthenticationError(
        "rejected"
    )
    with pytest.raises(HomeAssistantError, match="cached status is not"):
        await coordinator._async_poll_vehicles(mode="force")
    assert manager.api.force_refresh_vehicle_state.call_count == 2
    manager.api.update_vehicle_with_cached_state.assert_not_called()
    manager.api.refresh_access_token.assert_not_called()
    assert manager.token.valid_until < dt.datetime.now(dt.UTC)
    assert manager.token.mqtt_registration_uuid == "phone"


@pytest.mark.parametrize("mode", ["check", "force"])
async def test_rate_limit_propagates_without_cached_fallback(account, mode):
    coordinator, manager = account
    manager.api.force_refresh_vehicle_state.side_effect = RateLimitingError("limit")
    # The real manager only wakes stale cars.
    for vehicle in manager.vehicles.values():
        vehicle.last_updated_at = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    with pytest.raises(UpdateFailed) as caught:
        await coordinator._async_poll_vehicles(mode=mode)
    assert caught.value.retry_after == 2 * 60 * 60
    manager.api.force_refresh_vehicle_state.assert_called_once()
    manager.api.update_vehicle_with_cached_state.assert_not_called()


async def test_periodic_failed_wakeup_gets_cached_data_and_backoff(
    account, monkeypatch
):
    coordinator, manager = account
    clock = [100.0]
    monkeypatch.setattr(
        "custom_components.hyundai_jp.coordinator.time.monotonic", lambda: clock[0]
    )
    for vehicle in manager.vehicles.values():
        vehicle.last_updated_at = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    manager.api.force_refresh_vehicle_state.side_effect = ServiceTemporaryUnavailable(
        "asleep"
    )

    await coordinator._async_poll_vehicles(mode="check")
    assert manager.api.force_refresh_vehicle_state.call_count == 2
    assert manager.api.update_vehicle_with_cached_state.call_count == 2
    clock[0] += 10
    await coordinator._async_poll_vehicles(mode="check")
    assert manager.api.force_refresh_vehicle_state.call_count == 2
    assert manager.api.update_vehicle_with_cached_state.call_count == 4
    clock[0] += 1000
    await coordinator._async_poll_vehicles(mode="check")
    assert manager.api.force_refresh_vehicle_state.call_count == 4
    assert coordinator._force_refresh_retry_at["a"] == clock[0] + 1800


async def test_explicit_refresh_failure_reports_failed_fresh_read(account):
    coordinator, manager = account
    manager.api.force_refresh_vehicle_state.side_effect = ServiceTemporaryUnavailable(
        "asleep"
    )
    with pytest.raises(HomeAssistantError, match="Fresh vehicle status"):
        await coordinator._async_poll_vehicles(mode="force")
    manager.api.update_vehicle_with_cached_state.assert_not_called()


async def test_geocoding_transport_outage_does_not_abort_second_car(account):
    coordinator, manager = account
    api = HyundaiBluelinkApi()
    manager.api = api
    manager.geocode_api_enable = True
    api.update_vehicle_with_cached_state = Mock()
    api.session.get = Mock(side_effect=requests.Timeout("private coordinates"))
    for vehicle in manager.vehicles.values():
        vehicle.location = (0.0, 0.0, dt.datetime.now(dt.UTC))
    try:
        await coordinator._async_poll_vehicles(mode="cached")
        assert api.update_vehicle_with_cached_state.call_count == 2
        assert api.session.get.call_count == 2
        assert not coordinator._failed_vehicles
    finally:
        api.close()
