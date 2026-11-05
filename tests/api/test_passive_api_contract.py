"""Cached telemetry and protocol diagnostics remain local to the account."""

from unittest.mock import Mock

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.Vehicle import Vehicle
from custom_components.hyundai_jp._api.VehicleManager import VehicleManager


def test_japan_diagnostics_and_cached_state_do_not_make_requests():
    api = HyundaiBluelinkApi()
    api.session = Mock()
    before = Vehicle(id="synthetic")
    assert api.get_local_diagnostics()["mqtt_sessions"] == []
    assert api.apply_cached_mqtt_state(before) is False
    api.session.request.assert_not_called()


def test_vehicle_manager_exposes_protocol_contract_without_authentication():
    api = Mock(spec=HyundaiBluelinkApi)
    api.get_local_diagnostics.return_value = {"state": "local"}
    api.apply_cached_mqtt_state.return_value = True
    manager = VehicleManager.__new__(VehicleManager)
    manager.api = api
    vehicle = Vehicle(id="synthetic")
    manager.vehicles = {vehicle.id: vehicle}
    manager.check_and_refresh_token = Mock(side_effect=AssertionError("no login"))
    assert manager.get_local_diagnostics() == {"state": "local"}
    assert manager.apply_cached_mqtt_state(vehicle.id) is True
    api.apply_cached_mqtt_state.assert_called_once_with(vehicle)
    manager.check_and_refresh_token.assert_not_called()
