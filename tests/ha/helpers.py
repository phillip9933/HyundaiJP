"""Synthetic HA test builders, independent of collected test modules.

Coordinator builders run the production runtime-state initializer. They bypass
only HA scheduling/setup so operation tests can supply controlled executors and
managers without credentials, network requests or startup side effects.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp._api.VehicleManager import VehicleManager
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator


def make_runtime_coordinator() -> JapanDataUpdateCoordinator:
    """Initialize real per-account runtime state without starting HA polling."""
    result = JapanDataUpdateCoordinator.__new__(JapanDataUpdateCoordinator)
    result._initialize_runtime_state()
    result._listeners = {}
    return result


UNIQUE_ID = "uid"


def make_coordinator(*vehicles: Vehicle) -> MagicMock:
    """A coordinator stub holding ``vehicles``, keyed by ``vehicle.id``.

    Coroutine methods that platform setup awaits are AsyncMocks; add new ones
    here so every test picks them up.
    """
    coordinator = MagicMock()
    coordinator.vehicle_manager.vehicles = {v.id: v for v in vehicles}
    coordinator.async_supports_svm = AsyncMock(return_value=False)
    coordinator.get_pending_target.return_value = None
    coordinator.get_pending_status.return_value = None
    return coordinator


def make_hass(coordinator: MagicMock) -> tuple[MagicMock, MagicMock]:
    """A ``(hass, config_entry)`` pair wired to ``coordinator``."""
    hass = MagicMock()
    hass.data = {}
    config_entry = MagicMock(unique_id=UNIQUE_ID, runtime_data=coordinator)
    return hass, config_entry


def make_operation_coordinator() -> JapanDataUpdateCoordinator:
    """Supply controllable command/status jobs around real coordinator state."""
    result = make_runtime_coordinator()
    result.vehicle_manager = MagicMock()
    result.vehicle_manager.api = SimpleNamespace()
    result.vehicle_manager.apply_cached_mqtt_state.side_effect = lambda vehicle_id: (
        VehicleManager.apply_cached_mqtt_state(result.vehicle_manager, vehicle_id)
    )
    result.vehicle_manager.get_vehicle.side_effect = lambda vehicle_id: (
        result.vehicle_manager.vehicles[vehicle_id]
    )
    result.data = None
    result.hass = MagicMock()
    result.hass.async_add_executor_job = AsyncMock()
    result.async_check_and_refresh_token = AsyncMock()
    result.async_set_updated_data = MagicMock()
    result.async_refresh = AsyncMock()

    return result


def make_cleanup_coordinator(manager: Any) -> JapanDataUpdateCoordinator:
    """Supply a manager that owns API cleanup, matching the production contract."""
    result = make_runtime_coordinator()
    result.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda target: target())
    )
    if not hasattr(manager, "api"):
        manager.api = SimpleNamespace(close=Mock())
    owned_close = manager.close

    def close_owned_api():
        owned_close()
        manager.api.close()

    manager.close = Mock(side_effect=close_owned_api)
    result.vehicle_manager = manager
    return result


def make_unload_context(coordinator, *, platform_success=True):
    """Wire one account to successful or failed HA platform unloading."""
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=platform_success)
        ),
    )
    return hass, SimpleNamespace(unique_id="synthetic-entry", runtime_data=coordinator)
