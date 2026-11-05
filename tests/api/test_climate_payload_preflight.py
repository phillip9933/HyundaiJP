"""Restart validation builds the existing Japan envelope without side effects."""

from copy import deepcopy
from dataclasses import asdict
from unittest.mock import Mock

from custom_components.hyundai_jp._api import ClimateRequestOptions, Vehicle
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi


def test_climate_builder_matches_wire_payload_without_mutating_state():
    api = HyundaiBluelinkApi()
    vehicle = Vehicle(id="synthetic", air_control_is_on=True)
    vehicle.air_temperature = (21, "C")
    vehicle.supports.climate_temperature_range = (17, 27)
    api._vehicle_profiles[vehicle.id] = {"option": {"hvacTempType": 1}}
    api.session.request = Mock(side_effect=AssertionError("No network"))
    api._japan_command = Mock()
    options = ClimateRequestOptions(set_temp=23, climate=True, heating=1, defrost=True)
    before_vehicle, before_options = asdict(vehicle), asdict(options)
    before_profile = deepcopy(api._vehicle_profiles)
    try:
        assert api.build_climate_payload(vehicle, options) == {
            "command": "start",
            "hvacTemp": "23.0",
            "hvacTempType": 1,
            "tempUnit": "C",
            "heating1": 1,
            "windshieldFrontDefogState": True,
        }
        assert asdict(vehicle) == before_vehicle
        assert asdict(options) == before_options
        assert api._vehicle_profiles == before_profile
        api._japan_command.assert_not_called()
        api.session.request.assert_not_called()
    finally:
        api.close()
