"""Tests for syncing HA device enabled state to the library."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.hyundai_jp import coordinator as coordinator_module
from custom_components.hyundai_jp._api import Vehicle, VehicleManager
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator


def _sync(
    monkeypatch: pytest.MonkeyPatch,
    vehicles: list[Vehicle],
    devices: dict[tuple[str, str], MagicMock],
) -> VehicleManager:
    manager = VehicleManager.__new__(VehicleManager)
    manager.vehicles = {v.id: v for v in vehicles}

    def get_device(
        identifier: tuple[str, str], config_entry_id: str
    ) -> MagicMock | None:
        assert config_entry_id == "synthetic-entry"
        return devices.get(identifier)

    registry = MagicMock()
    registry.async_get_device_by_identifier.side_effect = get_device
    registry.async_get_device.side_effect = AssertionError("Unscoped device lookup")
    monkeypatch.setattr(coordinator_module.dr, "async_get", lambda _: registry)

    coordinator = JapanDataUpdateCoordinator.__new__(JapanDataUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.config_entry = SimpleNamespace(entry_id="synthetic-entry")
    coordinator.vehicle_manager = manager
    coordinator._sync_vehicle_enabled_states()
    return manager


def test_disabled_device_disables_vehicle(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _sync(
        monkeypatch,
        [Vehicle(id="v1"), Vehicle(id="v2")],
        {
            (DOMAIN, "v1"): MagicMock(disabled=True),
            (DOMAIN, "v2"): MagicMock(disabled=False),
        },
    )
    assert manager.vehicles["v1"].enabled is False
    assert manager.vehicles["v2"].enabled is True


def test_reenabled_device_enables_vehicle(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _sync(
        monkeypatch,
        [Vehicle(id="v1", enabled=False)],
        {(DOMAIN, "v1"): MagicMock(disabled=False)},
    )
    assert manager.vehicles["v1"].enabled is True


def test_device_matched_by_vin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A device registered under an old vehicle id still matches by VIN."""
    manager = _sync(
        monkeypatch,
        [Vehicle(id="new-id", VIN="KMH12345678901234")],
        {(DOMAIN, "vin:KMH12345678901234"): MagicMock(disabled=True)},
    )
    assert manager.vehicles["new-id"].enabled is False


def test_vehicle_without_device_stays_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _sync(monkeypatch, [Vehicle(id="v1")], {})
    assert manager.vehicles["v1"].enabled is True
