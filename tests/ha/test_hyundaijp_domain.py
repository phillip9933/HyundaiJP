"""Independent HyundaiJP setup and services leave the old integration intact."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components import hyundai_jp
from custom_components.hyundai_jp import services
from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.entity import HyundaiKiaConnectEntity


async def test_setup_and_unload_only_the_new_domain(monkeypatch):
    old_coordinator = SimpleNamespace(async_shutdown=AsyncMock())
    coordinator = SimpleNamespace(
        async_config_entry_first_refresh=AsyncMock(), async_shutdown=AsyncMock()
    )
    factory = MagicMock(return_value=coordinator)
    monkeypatch.setattr(hyundai_jp, "JapanDataUpdateCoordinator", factory)
    # Retain real service registration so the domain is part of the contract.
    hass = SimpleNamespace(
        data={"kia_uvo": {"old-entry": old_coordinator}},
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        ),
        services=MagicMock(),
    )
    entry = SimpleNamespace(unique_id="new-entry", domain=DOMAIN)
    assert await hyundai_jp.async_setup_entry(hass, entry)
    factory.assert_called_once_with(hass, entry)
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, hyundai_jp.PLATFORMS
    )
    assert hass.data[DOMAIN]["new-entry"] is coordinator
    assert {call.args[0] for call in hass.services.async_register.call_args_list} == {
        DOMAIN
    }
    assert hass.services.async_register.call_count == len(services.SUPPORTED_SERVICES)
    assert await hyundai_jp.async_unload_entry(hass, entry)
    coordinator.async_shutdown.assert_awaited_once()
    old_coordinator.async_shutdown.assert_not_awaited()
    assert hass.data["kia_uvo"]["old-entry"] is old_coordinator
    assert hass.data[DOMAIN] == {}
    assert {call.args[0] for call in hass.services.async_remove.call_args_list} == {
        DOMAIN
    }


def test_manifest_uses_the_bundled_api_and_independent_branding():
    component = Path(hyundai_jp.__file__).parent
    manifest = json.loads((component / "manifest.json").read_text())
    assert manifest["domain"] == DOMAIN == "hyundai_jp"
    assert manifest["name"] == "HyundaiJP"
    assert manifest["codeowners"] == ["@phillip9933"]
    assert manifest["documentation"] == "https://github.com/phillip9933/HyundaiJP"
    assert all("hyundai_kia_connect_api" not in dep for dep in manifest["requirements"])
    assert not list(component.glob("*.whl"))
    for source in component.glob("*.py"):
        assert "from hyundai_kia_connect_api" not in source.read_text()
    assert "integration: kia_uvo" not in (component / "services.yaml").read_text()


def test_device_identity_has_its_own_namespace():
    vehicle = Vehicle(id="synthetic", name="synthetic", model="IONIQ 5")
    vehicle.VIN = "SYNTHETIC_TEST_VIN"
    device = HyundaiKiaConnectEntity(MagicMock(), vehicle).device_info
    assert device["manufacturer"] == "Hyundai Japan"
    assert device["identifiers"] == {
        (DOMAIN, "synthetic"),
        (DOMAIN, "vin:SYNTHETIC_TEST_VIN"),
    }
