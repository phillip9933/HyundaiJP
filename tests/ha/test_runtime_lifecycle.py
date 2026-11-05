"""Real HA entries own runtimes; service registration survives unload/reload."""

from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er, frame
import pytest

from custom_components import hyundai_jp
from custom_components.hyundai_jp import config_flow, lock, services
from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.const import DOMAIN
from tests.ha.helpers import make_coordinator


def _entry(account):
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Synthetic account",
        data={"username": account, "password": "synthetic", "pin": "0000"},
        options={},
        unique_id=f"synthetic-{account}",
        source="user",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


async def test_real_entry_runtime_reload_and_services_lifetime(monkeypatch, tmp_path):
    hass = HomeAssistant(str(tmp_path))
    frame.async_setup(hass)
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    entries = [_entry("a"), _entry("b")]
    integration = loader.Integration(
        hass,
        "custom_components.hyundai_jp",
        Path(hyundai_jp.__file__).parent,
        {"domain": DOMAIN, "name": "Synthetic Japan integration", "version": "0.6.0"},
    )
    hass.data[loader.DATA_INTEGRATIONS][DOMAIN] = integration
    monkeypatch.setattr(
        integration, "async_get_component", AsyncMock(return_value=hyundai_jp)
    )
    monkeypatch.setattr(
        integration, "async_get_platform", AsyncMock(return_value=config_flow)
    )
    coordinators = []

    def create_coordinator(_hass, entry):
        vehicle = Vehicle(id=f"vehicle-{entry.unique_id[-1]}")
        owner = make_coordinator(vehicle)
        owner.config_entry = entry
        owner.async_config_entry_first_refresh = AsyncMock()
        owner.async_shutdown = AsyncMock()
        owner.async_unlock_vehicle = AsyncMock()
        coordinators.append(owner)
        return owner

    monkeypatch.setattr(hyundai_jp, "JapanDataUpdateCoordinator", create_coordinator)
    monkeypatch.setattr(ConfigEntry, "async_migrate", AsyncMock(return_value=True))
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    )
    register = Mock(wraps=hass.services.async_register)
    monkeypatch.setattr(
        type(hass.services),
        "async_register",
        lambda self, *args, **kwargs: register(*args, **kwargs),
    )
    await hyundai_jp.async_setup(hass, {})
    handlers = hass.services.async_services()[DOMAIN].copy()

    async def load(entry):
        entry.supports_unload = True
        entry.supports_remove_device = True
        hass.config_entries._entries[entry.entry_id] = entry
        async with entry.setup_lock:
            await entry.async_setup(hass, integration=integration)
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.config_entry is entry

    async def unload(entry):
        async with entry.setup_lock:
            assert await entry.async_unload(hass, integration=integration)
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert not hasattr(entry, "runtime_data")

    await load(entries[0])
    added = []
    await lock.async_setup_entry(hass, entries[0], added.extend)
    assert added[0].coordinator is entries[0].runtime_data
    assert added[0].unique_id == "hyundai_jp_vehicle-a_door_lock"
    await hass.services.async_call(DOMAIN, services.SERVICE_UNLOCK, {}, blocking=True)
    coordinators[0].async_unlock_vehicle.assert_awaited_once_with("vehicle-a")
    await load(entries[1])
    with pytest.raises(HomeAssistantError, match="Select a vehicle device"):
        await hass.services.async_call(
            DOMAIN, services.SERVICE_UNLOCK, {}, blocking=True
        )
    await unload(entries[0])
    await hass.services.async_call(DOMAIN, services.SERVICE_UNLOCK, {}, blocking=True)
    coordinators[1].async_unlock_vehicle.assert_awaited_once_with("vehicle-b")
    await unload(entries[1])
    with pytest.raises(HomeAssistantError, match="Select a vehicle device"):
        await hass.services.async_call(
            DOMAIN, services.SERVICE_UNLOCK, {}, blocking=True
        )
    await load(entries[0])
    await hass.services.async_call(DOMAIN, services.SERVICE_UNLOCK, {}, blocking=True)
    coordinators[2].async_unlock_vehicle.assert_awaited_once_with("vehicle-a")
    added_after_reload = []
    await lock.async_setup_entry(hass, entries[0], added_after_reload.extend)
    assert added_after_reload[0].unique_id == added[0].unique_id
    assert entries[0].unique_id == "synthetic-a"
    assert DOMAIN not in hass.data
    assert register.call_count == len(services.SUPPORTED_SERVICES)
    assert hass.services.async_services()[DOMAIN] == handlers
    await unload(entries[0])
    for owner in coordinators:
        owner.async_shutdown.assert_awaited_once()
    await hass.async_block_till_done()
