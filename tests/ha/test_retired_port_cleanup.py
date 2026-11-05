"""Remove only obsolete port switches, including after a version rollback."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components import hyundai_jp
from custom_components.hyundai_jp.const import DOMAIN


def account_entry():
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Synthetic",
        data={},
        options={},
        unique_id=None,
        source="user",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )


async def test_setup_removes_only_own_retired_switches_and_handles_rollback(
    monkeypatch, tmp_path
):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    first, second = account_entry(), account_entry()
    for entry in (first, second):
        hass.config_entries._entries[entry.entry_id] = entry
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    registry = er.async_get(hass)
    retired_id = f"{DOMAIN}_car_ev_charge_port_door_is_open"

    def register(domain, platform, unique_id, owner=first, **kwargs):
        return registry.async_get_or_create(
            domain,
            platform,
            unique_id,
            config_entry=owner,
            config_subentry_id=None,
            object_id_base="custom_named_port",
            **kwargs,
        ).entity_id

    retired = {
        register("switch", DOMAIN, retired_id),
        register(
            "switch",
            DOMAIN,
            f"{DOMAIN}_second_car_ev_charge_port_door_is_open",
            disabled_by=er.RegistryEntryDisabler.USER,
        ),
    }
    retained = {
        register("button", DOMAIN, f"{DOMAIN}_car_charge_port_toggle"),
        register("cover", DOMAIN, f"{DOMAIN}_car_charge_port"),
        register("binary_sensor", DOMAIN, retired_id),
        register("switch", DOMAIN, f"{DOMAIN}_car_ev_battery_is_charging"),
        register(
            "switch",
            DOMAIN,
            f"{DOMAIN}_other_car_ev_charge_port_door_is_open",
            owner=second,
        ),
        register("switch", "other_integration", retired_id),
        register("switch", DOMAIN, "other_prefix_ev_charge_port_door_is_open"),
    }
    coordinator = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
    monkeypatch.setattr(
        hyundai_jp, "JapanDataUpdateCoordinator", Mock(return_value=coordinator)
    )
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())

    assert await hyundai_jp.async_setup_entry(hass, first)
    assert set(registry.entities) == retained
    assert retired.isdisjoint(registry.entities)
    # A rollback can recreate the switch; a later upgrade still removes it.
    register("switch", DOMAIN, retired_id)
    assert await hyundai_jp.async_setup_entry(hass, first)
    assert set(registry.entities) == retained
    # Repeated setup without an obsolete entry is harmless.
    assert await hyundai_jp.async_setup_entry(hass, first)
    assert set(registry.entities) == retained
    await hass.async_block_till_done()
