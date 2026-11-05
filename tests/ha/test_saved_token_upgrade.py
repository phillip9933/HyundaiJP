"""Saved HyundaiJP entries survive removal of fixed region/brand selectors."""

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from homeassistant.helpers import frame
from homeassistant.helpers.device_registry import DATA_REGISTRY

from custom_components.hyundai_jp._api import Token, Vehicle
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator


async def test_saved_token_upgrade_ignores_old_metadata_without_reauth(monkeypatch):
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)
    token = Token(
        username="synthetic-user",
        password="synthetic-password",
        pin="1234",
        access_token="synthetic-access-token",
        refresh_token="synthetic-refresh-token",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        device_id="synthetic-device",
        mqtt_registration_uuid="synthetic-registration",
        mqtt_client_id="synthetic-client",
    )
    data = {
        "region": 10,
        "brand": 2,
        "username": token.username,
        "password": token.password,
        "pin": token.pin,
        "token": token.to_dict(),
    }
    entry = SimpleNamespace(
        entry_id="synthetic-entry",
        async_on_unload=Mock(),
        unique_id="synthetic-account",
        data=data,
        options={"enable_geolocation_entity": True, "use_email_with_geocode_api": True},
    )
    hass = MagicMock()
    hass.data = {
        DATA_REGISTRY: SimpleNamespace(
            async_get_device_by_identifier=lambda *args, **kwargs: None
        )
    }

    async def executor(target, *args):
        return target(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=executor)
    coordinator = JapanDataUpdateCoordinator(hass, entry)
    coordinator.config_entry = entry
    manager = coordinator.vehicle_manager
    vehicle = Vehicle(id="synthetic-vehicle")
    login = Mock(side_effect=AssertionError("Saved token must not trigger login"))
    refresh = Mock(side_effect=AssertionError("Valid token must not be refreshed"))
    get_vehicles = Mock(return_value=[vehicle])
    monkeypatch.setattr(manager.api, "login", login)
    monkeypatch.setattr(manager.api, "refresh_access_token", refresh)
    monkeypatch.setattr(manager.api, "get_vehicles", get_vehicles)
    try:
        await coordinator.async_check_and_refresh_token()

        assert manager.token.to_dict() == token.to_dict()
        assert manager.vehicles == {vehicle.id: vehicle}
        assert manager.geocode_api_enable is True
        assert manager.geocode_api_use_email is True
        get_vehicles.assert_called_once_with(manager.token)
        login.assert_not_called()
        refresh.assert_not_called()
        hass.config_entries.async_update_entry.assert_not_called()
        assert entry.data == data
    finally:
        manager.close()
