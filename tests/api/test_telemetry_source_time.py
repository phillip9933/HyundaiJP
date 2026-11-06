"""Source timestamps remain required at the client boundary after parser cleanup."""

import copy
import datetime as dt
from unittest.mock import Mock

import pytest

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.exceptions import InvalidAPIResponseError
from custom_components.hyundai_jp._api.telemetry import JapanTelemetry
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.Vehicle import Vehicle


@pytest.mark.parametrize("protocol", [0, 1])
@pytest.mark.parametrize("timestamp", [None, "", "bad", 123, {}])
def test_invalid_source_timestamp_cannot_publish_or_make_old_data_fresh(
    protocol, timestamp
):
    api = HyundaiBluelinkApi()
    api.update_vehicle_with_cached_location = Mock()
    token = Token(username="synthetic", password="synthetic")
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=protocol)
    source_date = "20261106010000" if protocol else "20261106100000"
    state = (
        {
            "Date": source_date,
            "Drivetrain": {"FuelSystem": {"DTE": {"Total": 200, "Unit": 1}}},
        }
        if protocol
        else {"time": source_date, "doorLock": True}
    )

    def respond():
        payload = (
            {"state": {"Vehicle": state}}
            if protocol
            else {"vehicleStatusInfo": {"vehicleStatus": state}}
        )
        response = Mock(status_code=200)
        response.json.return_value = copy.deepcopy(
            {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": payload,
            }
        )
        api.session.request = Mock(return_value=response)

    try:
        respond()
        api.update_vehicle_with_cached_state(token, vehicle)
        assert vehicle.last_updated_at == dt.datetime(2026, 11, 6, 1, tzinfo=dt.UTC)
        before = copy.deepcopy(vehicle.__dict__)
        state["Date" if protocol else "time"] = timestamp
        respond()
        with pytest.raises(InvalidAPIResponseError):
            api.update_vehicle_with_cached_state(token, vehicle)
        assert vehicle.__dict__ == before
    finally:
        api.close()


@pytest.mark.parametrize("protocol", [0, 1])
def test_pure_parser_has_no_local_clock_fallback_for_missing_source_time(protocol):
    parser = JapanTelemetry()
    vehicle = Vehicle(id="synthetic")
    if protocol:
        parser._update_vehicle_properties_ccs2(
            vehicle, {"Drivetrain": {"FuelSystem": {"DTE": {"Total": 200, "Unit": 1}}}}
        )
    else:
        parser._update_vehicle_properties(vehicle, {"status": {}})
    assert vehicle.last_updated_at is None


@pytest.mark.parametrize("code,expected", [("06H", 17.0), ("0CH", 20.0), ("1AH", 27.0)])
def test_legacy_client_keeps_profile_temperature_decoding_without_parser_scale(
    code, expected
):
    api = HyundaiBluelinkApi()
    api.update_vehicle_with_cached_location = Mock()
    api._vehicle_profiles["synthetic"] = {"option": {"hvacTempType": 1}}
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    response = Mock(status_code=200)
    response.json.return_value = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "vehicleStatusInfo": {
                "vehicleStatus": {
                    "time": "20261106100000",
                    "doorLock": True,
                    "airTemp": {"unit": 0, "value": code},
                }
            }
        },
    }
    api.session.request = Mock(return_value=response)
    try:
        api.update_vehicle_with_cached_state(
            Token(username="synthetic", password="synthetic"), vehicle
        )
        assert vehicle.air_temperature == expected
        assert vehicle._air_temperature_unit == "°C"
        assert vehicle.is_locked is True
    finally:
        api.close()
