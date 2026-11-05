"""VehicleManager push callbacks and fleet ownership use Japan providers only."""

from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.hyundai_jp._api import VehicleManager
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.Vehicle import Vehicle
from tests.api.test_mqtt_provider_lifecycle import Provider


def manager():
    fleet = VehicleManager(10, 2, "synthetic", "synthetic", "0000")
    fleet.token = Token(username="synthetic", password="synthetic")
    fleet.vehicles = {key: Vehicle(id=key) for key in ("first", "second")}
    providers = {key: Provider() for key in fleet.vehicles}
    api = SimpleNamespace(
        get_push_provider=Mock(side_effect=lambda token, car: providers[car.id]),
        close=Mock(),
    )
    fleet.api = api
    return fleet, providers


def test_start_push_uses_vehicle_scoped_provider():
    fleet, providers = manager()
    assert fleet.start_push("first")
    assert fleet.is_push_connected
    fleet.api.get_push_provider.assert_called_once_with(
        fleet.token, fleet.vehicles["first"]
    )
    assert providers["first"].is_connected
    assert not providers["second"].is_connected
    fleet.close()


def test_start_push_false_when_vehicle_unknown_or_unsupported():
    fleet, _ = manager()
    assert not fleet.start_push("missing")
    fleet.vehicles["first"].supports.mqtt_push = False
    assert not fleet.start_push("first")
    fleet.api.get_push_provider.assert_not_called()
    assert fleet._push_session is None
    fleet.close()


def test_start_push_attempts_when_support_is_unknown():
    fleet, _ = manager()
    assert fleet.vehicles["first"].supports.mqtt_push is None
    assert fleet.start_push("first")
    fleet.close()


def test_multiple_vehicles_keep_independent_providers_and_callbacks():
    fleet, providers = manager()
    changed = Mock()
    fleet.set_push_status_callback(changed)
    assert fleet.start_push("first")
    assert fleet.start_push("second")
    providers["first"].status("first", "Status")
    providers["second"].status("second", "Status")
    assert changed.call_args_list == [
        (("first", "Status"),),
        (("second", "Status"),),
    ]
    providers["first"].status("second", "Status")
    providers["first"].status("missing", "Status")
    assert changed.call_count == 2
    fleet.stop_push()
    assert not fleet.is_push_connected
    assert all(provider.stop_count == 1 for provider in providers.values())
    fleet.close()


def test_start_failure_rolls_back_only_the_failed_vehicle():
    fleet, providers = manager()
    assert fleet.start_push("first")
    providers["second"].start_push = Mock(side_effect=ValueError("synthetic"))
    with pytest.raises(ValueError, match="synthetic"):
        fleet.start_push("second")
    assert fleet._push_session._vehicle_ids == {"first"}
    assert fleet._push_session._providers == {"first": providers["first"]}
    assert providers["second"].stop_count == 1
    assert providers["first"].is_connected
    fleet.close()


def test_provider_can_report_start_unavailable():
    fleet, providers = manager()
    providers["first"].start_push = Mock(return_value=False)
    assert not fleet.start_push("first")
    assert not fleet.is_push_connected
    fleet.close()


def test_callbacks_can_be_changed_after_start_and_cleared():
    fleet, providers = manager()
    assert fleet.start_push("first")
    changed, connected = Mock(), Mock()
    fleet.set_push_status_callback(changed)
    fleet.set_push_connection_change_callback(connected)
    providers["first"].status("first", "Status")
    providers["first"].connection(True)
    changed.assert_called_once_with("first", "Status")
    connected.assert_called_once_with(True)
    fleet.set_push_status_callback(None)
    fleet.set_push_connection_change_callback(None)
    providers["first"].status("first", "Status")
    providers["first"].connection(True)
    assert changed.call_count == connected.call_count == 1
    fleet.close()


def test_callbacks_set_before_start_are_forwarded_to_provider():
    fleet, providers = manager()
    changed, connected = Mock(), Mock()
    fleet.set_push_status_callback(changed)
    fleet.set_push_connection_change_callback(connected)
    assert fleet.start_push("first")
    connected.assert_called_once_with(True)
    providers["first"].status("first", "Status")
    changed.assert_called_once_with("first", "Status")
    fleet.close()


def test_stop_cancels_reconnect_and_drops_stale_callbacks():
    fleet, providers = manager()
    changed = Mock()
    fleet.set_push_status_callback(changed)
    assert fleet.start_push("first")
    cancel = Event()
    fleet._push_session._reconnect_cancel = cancel
    fleet.stop_push()
    assert cancel.is_set()
    providers["first"].status("first", "Status")
    changed.assert_not_called()
    assert not fleet._push_session._vehicle_ids
    assert not fleet._push_session._providers
    assert not fleet.is_push_connected
    fleet.close()


def test_clean_disconnect_and_empty_fleet_do_not_schedule_reconnect():
    fleet, _ = manager()
    fleet._ensure_push_session()
    fleet._push_session._on_push_disconnect("synthetic")
    assert fleet._push_session._reconnect_cancel is None
    assert fleet.start_push("first")
    fleet._push_session._on_push_disconnect("clean disconnect")
    assert fleet._push_session._reconnect_cancel is None
    fleet.close()
