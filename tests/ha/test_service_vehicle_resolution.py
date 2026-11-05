"""Registered services must resolve an explicit target before any car operation."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp import services
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator


def _device(*entry_ids: str, vehicles: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        config_entries=set(entry_ids),
        identifiers={(DOMAIN, identifier) for identifier in vehicles},
    )


def _services(monkeypatch, accounts, devices=None):
    """Capture real registered handlers with mocked account coordinators."""
    entries, coordinators = {}, {}
    for entry_id, vehicle_ids in accounts.items():
        entry = SimpleNamespace(
            entry_id=entry_id, unique_id=f"uid-{entry_id}", domain=DOMAIN
        )
        entries[entry_id] = entry
        coordinator = MagicMock(spec=JapanDataUpdateCoordinator)
        coordinator.config_entry = entry
        coordinator.vehicle_manager = SimpleNamespace(
            vehicles={identifier: object() for identifier in vehicle_ids}
        )
        coordinator.async_supports_svm.return_value = True
        coordinators[entry_id] = coordinator
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            entries[entry_id].unique_id: coordinator
            for entry_id, coordinator in coordinators.items()
        }
    }
    hass.config_entries.async_get_entry.side_effect = entries.get
    devices = {} if devices is None else devices
    registry = MagicMock()
    registry.async_get.side_effect = devices.get
    monkeypatch.setattr(services.device_registry, "async_get", lambda _: registry)
    handlers = {}

    def register(domain, service, handler, *, schema=None):
        assert domain == DOMAIN
        handlers[service] = handler

    hass.services.async_register.side_effect = register
    services.async_setup_services(hass)
    assert set(handlers) == set(services.SUPPORTED_SERVICES)
    return SimpleNamespace(
        hass=hass,
        entries=entries,
        coordinators=coordinators,
        registry=registry,
        handlers=handlers,
    )


async def _call(environment, service, data=None):
    await environment.handlers[service](
        ServiceCall(environment.hass, DOMAIN, service, data or {})
    )


def _assert_no_operations(environment):
    for coordinator in environment.coordinators.values():
        assert coordinator.mock_calls == []


@pytest.mark.parametrize("service", services.SUPPORTED_SERVICES)
async def test_every_service_rejects_nonexistent_explicit_target_before_operations(
    monkeypatch, service
):
    environment = _services(monkeypatch, {"account-a": ("vehicle-a",)})
    with pytest.raises(
        HomeAssistantError, match="selected device was not found"
    ) as info:
        await _call(environment, service, {ATTR_DEVICE_ID: "synthetic-private-device"})
    _assert_no_operations(environment)
    environment.registry.async_get.assert_called_once_with("synthetic-private-device")
    assert "synthetic-private-device" not in str(info.value)


@pytest.mark.parametrize("service", [services.SERVICE_UNLOCK, services.SERVICE_UPDATE])
@pytest.mark.parametrize("device_id", [None, "", " ", [], ["device-a"], {}, 1])
async def test_invalid_explicit_values_never_use_single_car_fallback(
    monkeypatch, service, device_id
):
    environment = _services(monkeypatch, {"account-a": ("vehicle-a",)})
    with pytest.raises(HomeAssistantError, match="one valid vehicle device"):
        await _call(environment, service, {ATTR_DEVICE_ID: device_id})
    _assert_no_operations(environment)
    environment.registry.async_get.assert_not_called()


@pytest.mark.parametrize("service", [services.SERVICE_UNLOCK, services.SERVICE_UPDATE])
@pytest.mark.parametrize(
    "invalid",
    [
        "unrelated",
        "missing-entry",
        "unloaded",
        "stale-id",
        "vin-only",
        "no-unique-id",
        "stale-entry-owner",
        "no-entry-owner",
    ],
)
async def test_explicit_device_must_belong_to_a_loaded_account_and_current_vehicle(
    monkeypatch, service, invalid
):
    device = _device("account-a", vehicles=("vehicle-a",))
    environment = _services(
        monkeypatch, {"account-a": ("vehicle-a",)}, {"device-a": device}
    )
    if invalid == "unrelated":
        environment.entries["account-a"].domain = "unrelated_integration"
    elif invalid == "missing-entry":
        environment.entries.clear()
    elif invalid == "unloaded":
        environment.hass.data[DOMAIN].clear()
    elif invalid == "stale-id":
        device.identifiers = {(DOMAIN, "old-vehicle-id"), (DOMAIN, "vin:SYNTHETIC")}
    elif invalid == "vin-only":
        device.identifiers = {(DOMAIN, "vin:SYNTHETIC")}
    elif invalid == "no-unique-id":
        environment.entries["account-a"].unique_id = None
    elif invalid == "stale-entry-owner":
        environment.coordinators["account-a"].config_entry = SimpleNamespace(
            entry_id="replacement-entry-with-the-same-unique-id"
        )
    else:
        environment.coordinators["account-a"].config_entry = None
    with pytest.raises(
        HomeAssistantError, match="no active Hyundai Bluelink JP vehicle"
    ):
        await _call(environment, service, {ATTR_DEVICE_ID: "device-a"})
    _assert_no_operations(environment)
    environment.registry.async_get.assert_called_once_with("device-a")


@pytest.mark.parametrize("service", [services.SERVICE_UNLOCK, services.SERVICE_UPDATE])
@pytest.mark.parametrize("multiple_accounts", [False, True])
async def test_ambiguous_explicit_matches_are_rejected(
    monkeypatch, service, multiple_accounts
):
    accounts = (
        {"account-a": ("shared-vehicle",), "account-b": ("shared-vehicle",)}
        if multiple_accounts
        else {"account-a": ("vehicle-a", "vehicle-b")}
    )
    device = _device(
        *accounts,
        vehicles=("shared-vehicle",)
        if multiple_accounts
        else ("vehicle-a", "vehicle-b"),
    )
    environment = _services(monkeypatch, accounts, {"device": device})
    with pytest.raises(
        HomeAssistantError, match="multiple active Hyundai Bluelink JP vehicles"
    ):
        await _call(environment, service, {ATTR_DEVICE_ID: "device"})
    _assert_no_operations(environment)


async def test_explicit_current_backend_id_ignores_vin_and_stale_aliases(monkeypatch):
    environment = _services(
        monkeypatch,
        {"account-a": ("vehicle-a", "vehicle-b")},
        {
            "device-b": _device(
                "account-a", vehicles=("vehicle-b", "old-id", "vin:SYNTHETIC")
            )
        },
    )
    await _call(environment, services.SERVICE_UNLOCK, {ATTR_DEVICE_ID: "device-b"})
    environment.coordinators["account-a"].async_unlock_vehicle.assert_awaited_once_with(
        "vehicle-b"
    )
    environment.registry.async_get.assert_called_once_with("device-b")
    environment.hass.config_entries.async_get_entry.assert_called_once_with("account-a")


async def test_shared_backend_identity_is_scoped_to_selected_config_entry(monkeypatch):
    environment = _services(
        monkeypatch,
        {"account-a": ("shared-vehicle",), "account-b": ("shared-vehicle",)},
        {"device-b": _device("account-b", vehicles=("shared-vehicle",))},
    )
    await _call(environment, services.SERVICE_UNLOCK, {ATTR_DEVICE_ID: "device-b"})
    environment.coordinators["account-b"].async_unlock_vehicle.assert_awaited_once_with(
        "shared-vehicle"
    )
    assert environment.coordinators["account-a"].mock_calls == []
    environment.registry.async_get.assert_called_once_with("device-b")


async def test_stale_or_foreign_associations_do_not_hide_valid_current_target(
    monkeypatch,
):
    device = _device(
        "missing-entry", "foreign-entry", "account-a", vehicles=("vehicle-a",)
    )
    environment = _services(
        monkeypatch, {"account-a": ("vehicle-a",)}, {"device-a": device}
    )
    environment.entries["foreign-entry"] = SimpleNamespace(
        domain="unrelated", unique_id="other"
    )
    await _call(environment, services.SERVICE_LOCK, {ATTR_DEVICE_ID: "device-a"})
    environment.coordinators["account-a"].async_lock_vehicle.assert_awaited_once_with(
        "vehicle-a"
    )


async def test_single_car_without_target_keeps_convenience(monkeypatch):
    environment = _services(monkeypatch, {"account-a": ("vehicle-a",)})
    await _call(environment, services.SERVICE_UNLOCK)
    environment.coordinators["account-a"].async_unlock_vehicle.assert_awaited_once_with(
        "vehicle-a"
    )
    environment.registry.async_get.assert_not_called()


@pytest.mark.parametrize(
    "accounts",
    [
        {},
        {"account-a": ()},
        {"account-a": ("a", "b")},
        {"account-a": ("a",), "account-b": ("b",)},
    ],
)
async def test_vehicle_action_without_unambiguous_target_is_rejected(
    monkeypatch, accounts
):
    environment = _services(monkeypatch, accounts)
    with pytest.raises(HomeAssistantError, match="Select a vehicle device"):
        await _call(environment, services.SERVICE_UNLOCK)
    _assert_no_operations(environment)
    environment.registry.async_get.assert_not_called()


@pytest.mark.parametrize(
    "service,method",
    [
        (services.SERVICE_UPDATE, "async_update_all"),
        (services.SERVICE_FORCE_UPDATE, "async_force_update_all"),
    ],
)
async def test_account_refresh_without_device_keeps_all_car_scope(
    monkeypatch, service, method
):
    environment = _services(monkeypatch, {"account-a": ("vehicle-a", "vehicle-b")})
    await _call(environment, service)
    getattr(environment.coordinators["account-a"], method).assert_awaited_once_with()
    environment.registry.async_get.assert_not_called()


@pytest.mark.parametrize(
    "service,method",
    [
        (services.SERVICE_UPDATE, "async_update_all"),
        (services.SERVICE_FORCE_UPDATE, "async_force_update_all"),
    ],
)
async def test_account_refresh_with_explicit_device_selects_only_its_account(
    monkeypatch, service, method
):
    environment = _services(
        monkeypatch,
        {"account-a": ("vehicle-a",), "account-b": ("vehicle-b", "vehicle-c")},
        {"device-b": _device("account-b", vehicles=("vehicle-b",))},
    )
    await _call(environment, service, {ATTR_DEVICE_ID: "device-b"})
    getattr(environment.coordinators["account-b"], method).assert_awaited_once_with()
    assert environment.coordinators["account-a"].mock_calls == []
    environment.registry.async_get.assert_called_once_with("device-b")


@pytest.mark.parametrize(
    "service", [services.SERVICE_UPDATE, services.SERVICE_FORCE_UPDATE]
)
async def test_multiple_accounts_require_a_target_for_refresh(monkeypatch, service):
    environment = _services(monkeypatch, {"account-a": ("a",), "account-b": ("b",)})
    with pytest.raises(
        HomeAssistantError, match="identify the Hyundai Bluelink JP account"
    ):
        await _call(environment, service)
    _assert_no_operations(environment)


async def test_registered_navigation_handler_preserves_payload_mapping(monkeypatch):
    environment = _services(monkeypatch, {"account-a": ("vehicle-a",)})
    await _call(
        environment,
        services.SERVICE_SET_NAVIGATION,
        {
            "latitude": "35.6812",
            "longitude": "139.7671",
            "name": "Synthetic station",
            "address": "Synthetic address",
            "zip_code": "synthetic-postcode",
            "place_id": "synthetic-place",
        },
    )
    action = environment.coordinators["account-a"].async_set_navigation
    action.assert_awaited_once()
    vehicle_id, pois = action.await_args.args
    assert vehicle_id == "vehicle-a"
    assert pois[0].coord.lat == 35.6812 and pois[0].coord.lon == 139.7671
    assert pois[0].name == "Synthetic station"
    assert pois[0].addr == "Synthetic address"
    assert pois[0].zip == "synthetic-postcode"
    assert pois[0].place_id == "synthetic-place"
