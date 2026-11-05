"""Optional protocol surfaces remain passive across base interfaces."""

from unittest.mock import Mock

import pytest

from custom_components.hyundai_jp._api.ApiImpl import ApiImpl
from custom_components.hyundai_jp._api.ApiImplType1 import ApiImplType1
from custom_components.hyundai_jp._api.Vehicle import Vehicle
from custom_components.hyundai_jp._api.VehicleManager import VehicleManager


@pytest.mark.parametrize("api", [ApiImpl(), ApiImplType1()])
def test_base_interfaces_have_passive_defaults_without_network(api):
    api.session = Mock()
    before = Vehicle(id="synthetic")
    assert api.supported_commands is None
    assert api.get_local_diagnostics() == {}
    assert api.apply_cached_mqtt_state(before) is False
    api.session.request.assert_not_called()


def test_vehicle_manager_exposes_protocol_contract_without_authentication():
    api = Mock(spec=ApiImpl)
    api.supported_commands = frozenset({"lock_action"})
    api.get_local_diagnostics.return_value = {"state": "local"}
    api.apply_cached_mqtt_state.return_value = True
    manager = VehicleManager.__new__(VehicleManager)
    manager.api = api
    vehicle = Vehicle(id="synthetic")
    manager.vehicles = {vehicle.id: vehicle}
    manager.check_and_refresh_token = Mock(side_effect=AssertionError("no login"))
    assert manager.supported_commands == frozenset({"lock_action"})
    assert manager.get_local_diagnostics() == {"state": "local"}
    assert manager.apply_cached_mqtt_state(vehicle.id) is True
    api.apply_cached_mqtt_state.assert_called_once_with(vehicle)
    manager.check_and_refresh_token.assert_not_called()
