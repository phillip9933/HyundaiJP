"""Disabled-state lookup must stay inside the current Bluelink account."""

from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.hyundai_jp import coordinator as coordinator_module
from custom_components.hyundai_jp._api import Vehicle, VehicleManager
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator

VIN = "SYNTHETIC_SHARED_VIN"


def _device(
    entry_id: str, identifiers: set[tuple[str, str]], *, disabled: bool
) -> dr.DeviceEntry:
    return dr.DeviceEntry(
        config_entry_id=entry_id,
        identifiers=identifiers,
        disabled_by=dr.DeviceEntryDisabler.USER if disabled else None,
    )


def _registry(*devices: dr.DeviceEntry) -> dr.DeviceRegistry:
    # Exercise HA's real per-config-entry identifier index and lookup method.
    # This path needs no registry storage, HA startup or network access.
    registry = dr.DeviceRegistry.__new__(dr.DeviceRegistry)
    registry._devices = dr.ActiveDeviceRegistryItems()
    for device in devices:
        registry._devices[device.id] = device
    registry.async_get_device = Mock(
        side_effect=AssertionError("Global identifier lookup is forbidden")
    )
    return registry


def _coordinator(
    monkeypatch: pytest.MonkeyPatch,
    registry: dr.DeviceRegistry,
    entry_id: str,
    vehicle: Vehicle,
) -> JapanDataUpdateCoordinator:
    manager = VehicleManager.__new__(VehicleManager)
    manager.vehicles = {vehicle.id: vehicle}
    coordinator = JapanDataUpdateCoordinator.__new__(JapanDataUpdateCoordinator)
    coordinator.hass = Mock()
    # entry_id scopes registry ownership; unique_id is deliberately different.
    coordinator.config_entry = SimpleNamespace(
        entry_id=entry_id, unique_id=f"unique-{entry_id}"
    )
    coordinator.vehicle_manager = manager
    monkeypatch.setattr(coordinator_module.dr, "async_get", lambda _: registry)
    return coordinator


@pytest.mark.parametrize("reverse_order", [False, True])
def test_accounts_sharing_vehicle_identity_keep_their_own_disabled_state(
    monkeypatch: pytest.MonkeyPatch, reverse_order: bool
) -> None:
    identifiers = {(DOMAIN, "shared-backend"), (DOMAIN, f"vin:{VIN}")}
    devices = [
        _device("account-a", identifiers, disabled=True),
        _device("account-b", identifiers, disabled=False),
    ]
    registry = _registry(*(reversed(devices) if reverse_order else devices))
    first = Vehicle(id="shared-backend", VIN=VIN)
    second = Vehicle(id="shared-backend", VIN=VIN, enabled=False)

    _coordinator(
        monkeypatch, registry, "account-a", first
    )._sync_vehicle_enabled_states()
    _coordinator(
        monkeypatch, registry, "account-b", second
    )._sync_vehicle_enabled_states()

    assert first.enabled is False
    assert second.enabled is True


def test_replaced_backend_id_falls_back_to_this_accounts_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        _device(
            "other-account",
            {(DOMAIN, "new-backend"), (DOMAIN, f"vin:{VIN}")},
            disabled=False,
        ),
        _device(
            "this-account",
            {(DOMAIN, "old-backend"), (DOMAIN, f"vin:{VIN}")},
            disabled=True,
        ),
    )
    vehicle = Vehicle(id="new-backend", VIN=VIN)

    _coordinator(
        monkeypatch, registry, "this-account", vehicle
    )._sync_vehicle_enabled_states()

    assert vehicle.enabled is False


@pytest.mark.parametrize("backend_disabled", [False, True])
def test_current_backend_match_takes_priority_over_conflicting_vin_match(
    monkeypatch: pytest.MonkeyPatch, backend_disabled: bool
) -> None:
    registry = _registry(
        _device("account", {(DOMAIN, "current")}, disabled=backend_disabled),
        _device("account", {(DOMAIN, f"vin:{VIN}")}, disabled=not backend_disabled),
    )
    lookup = Mock(wraps=registry.async_get_device_by_identifier)
    monkeypatch.setattr(registry, "async_get_device_by_identifier", lookup)
    vehicle = Vehicle(id="current", VIN=VIN, enabled=backend_disabled)

    _coordinator(
        monkeypatch, registry, "account", vehicle
    )._sync_vehicle_enabled_states()

    assert vehicle.enabled is not backend_disabled
    lookup.assert_called_once_with((DOMAIN, "current"), "account")


@pytest.mark.parametrize("vin", [None, ""])
def test_absent_vin_uses_only_the_current_accounts_backend_identifier(
    monkeypatch: pytest.MonkeyPatch, vin: str | None
) -> None:
    registry = _registry(
        _device("other-account", {(DOMAIN, "backend")}, disabled=False),
        _device("this-account", {(DOMAIN, "backend")}, disabled=True),
    )
    lookup = Mock(wraps=registry.async_get_device_by_identifier)
    monkeypatch.setattr(registry, "async_get_device_by_identifier", lookup)
    vehicle = Vehicle(id="backend", VIN=vin)

    _coordinator(
        monkeypatch, registry, "this-account", vehicle
    )._sync_vehicle_enabled_states()

    assert vehicle.enabled is False
    lookup.assert_called_once_with((DOMAIN, "backend"), "this-account")


def test_another_accounts_device_does_not_disable_an_unregistered_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        _device(
            "other-account",
            {(DOMAIN, "backend"), (DOMAIN, f"vin:{VIN}")},
            disabled=True,
        )
    )
    vehicle = Vehicle(id="backend", VIN=VIN, enabled=False)

    _coordinator(
        monkeypatch, registry, "this-account", vehicle
    )._sync_vehicle_enabled_states()

    assert vehicle.enabled is True
