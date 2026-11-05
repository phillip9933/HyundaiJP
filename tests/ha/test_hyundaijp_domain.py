"""Independent Hyundai Bluelink JP setup and services leave the old integration intact."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components import hyundai_jp
from custom_components.hyundai_jp import services
from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.entity import HyundaiBluelinkEntity


async def test_setup_and_unload_only_the_new_domain(monkeypatch):
    monkeypatch.setattr(hyundai_jp.er, "async_get", MagicMock())
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
    entry = SimpleNamespace(entry_id="new-entry", unique_id="new-entry", domain=DOMAIN)
    assert await hyundai_jp.async_setup(hass, {})
    assert await hyundai_jp.async_setup_entry(hass, entry)
    factory.assert_called_once_with(hass, entry)
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, hyundai_jp.PLATFORMS
    )
    assert entry.runtime_data is coordinator
    assert {call.args[0] for call in hass.services.async_register.call_args_list} == {
        DOMAIN
    }
    assert hass.services.async_register.call_count == len(services.SUPPORTED_SERVICES)
    assert await hyundai_jp.async_unload_entry(hass, entry)
    coordinator.async_shutdown.assert_awaited_once()
    old_coordinator.async_shutdown.assert_not_awaited()
    assert hass.data["kia_uvo"]["old-entry"] is old_coordinator
    assert DOMAIN not in hass.data
    hass.services.async_remove.assert_not_called()


def test_manifest_uses_the_bundled_api_and_independent_branding():
    component = Path(hyundai_jp.__file__).parent
    manifest = json.loads((component / "manifest.json").read_text())
    assert manifest["domain"] == DOMAIN == "hyundai_jp"
    assert manifest["name"] == "Hyundai Bluelink JP"
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
    device = HyundaiBluelinkEntity(MagicMock(), vehicle).device_info
    assert device["manufacturer"] == "Hyundai Japan"
    assert device["identifiers"] == {
        (DOMAIN, "synthetic"),
        (DOMAIN, "vin:SYNTHETIC_TEST_VIN"),
    }
    assert device["serial_number"] == "SYNTHETIC_TEST_VIN"


@pytest.mark.parametrize("vin", [None, ""])
def test_missing_vin_does_not_create_a_literal_serial_number(vin):
    vehicle = Vehicle(id="synthetic", name="synthetic", model="IONIQ 5", VIN=vin)
    device = HyundaiBluelinkEntity(MagicMock(), vehicle).device_info
    assert "serial_number" not in device
    assert device["identifiers"] == {(DOMAIN, "synthetic")}


def test_runtime_requirements_allow_home_assistant_core_versions():
    from packaging.requirements import Requirement

    manifest = json.loads(
        (Path(hyundai_jp.__file__).parent / "manifest.json").read_text()
    )
    requirements = {
        requirement.name.lower(): requirement
        for value in manifest["requirements"]
        for requirement in [Requirement(value)]
    }
    # HA supplies these directly and hassfest rejects listing them again.
    assert "requests" not in requirements
    assert "pillow" not in requirements
    # Additional integrations must accept HA's constrained package versions.
    assert "2.1.0" in requirements["paho-mqtt"].specifier
    assert "2.3.2" in requirements["numpy"].specifier
    assert manifest["categories"] == ["vehicles_transport.cars_evs"]
