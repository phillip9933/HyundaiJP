"""Optional cloud/geocoder failures must not discard good vehicle telemetry."""

import datetime as dt
from unittest.mock import Mock

import pytest
import requests

from custom_components.hyundai_jp._api import Token, Vehicle
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.const import ENGINE_TYPES
from custom_components.hyundai_jp._api.exceptions import (
    AuthenticationError,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)


@pytest.fixture
def api():
    result = HyundaiBluelinkApi()
    result.session.request = Mock(return_value=Mock(status_code=200))
    result.session.request.return_value.json.return_value = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "vehicleStatusInfo": {
                "vehicleStatus": {"time": "20261014090000", "doorLock": True}
            }
        },
    }
    yield result
    result.close()


@pytest.mark.parametrize(
    "section,method",
    [
        ("location", "update_vehicle_with_cached_location"),
        ("history", "update_vehicle_with_driving_info"),
        ("charge_targets", "update_vehicle_with_charge_targets"),
    ],
)
@pytest.mark.parametrize("error", [RequestTimeoutError, ServiceTemporaryUnavailable])
def test_optional_failure_preserves_core_and_uses_retry_cooldown(
    api, monkeypatch, section, method, error
):
    clock = [100.0]
    monkeypatch.setattr(
        "custom_components.hyundai_jp._api.client.time.monotonic", lambda: clock[0]
    )
    for name in (
        "update_vehicle_with_cached_location",
        "update_vehicle_with_driving_info",
        "update_vehicle_with_charge_targets",
    ):
        setattr(api, name, Mock())
    reader = getattr(api, method)
    reader.side_effect = error("PRIVATE_OPTIONAL_RESPONSE")
    token = Token(username="synthetic", password="synthetic")
    vehicle = Vehicle(
        id="synthetic", ccu_ccs2_protocol_support=0, engine_type=ENGINE_TYPES.EV
    )
    vehicle.total_power_consumed = 12.5
    api.update_vehicle_with_cached_state(token, vehicle)
    assert vehicle.is_locked is True
    assert vehicle.last_updated_at is not None
    assert vehicle.total_power_consumed == 12.5
    assert reader.call_count == 1
    summary = api.get_local_diagnostics()["optional_reads"][section]
    assert summary == {
        "state": "temporarily_unavailable",
        "failures": 1,
        "retry_seconds": 300,
    }
    assert "PRIVATE" not in repr(api.get_local_diagnostics())
    clock[0] += 10
    api.update_vehicle_with_cached_state(token, vehicle)
    assert reader.call_count == 1
    clock[0] += 300
    api.update_vehicle_with_cached_state(token, vehicle)
    assert reader.call_count == 2


@pytest.mark.parametrize("error", [AuthenticationError, RateLimitingError])
def test_optional_auth_and_rate_failures_still_propagate_without_partial_publish(
    api, error
):
    api.update_vehicle_with_cached_location = Mock(side_effect=error("rejected"))
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    with pytest.raises(error):
        api.update_vehicle_with_cached_state(
            Token(username="synthetic", password="synthetic"), vehicle
        )
    assert vehicle.is_locked is None
    assert vehicle.last_updated_at is None


def test_geocoding_uses_per_car_cache_encoded_params_and_valid_zero_coordinates(api):
    token = Token(username="synthetic+tag@example.invalid", password="synthetic")
    first, second = Vehicle(id="one"), Vehicle(id="two")
    first.location = (0.0, 0.0, dt.datetime.now(dt.UTC))
    second.location = (1.0, 2.0, dt.datetime.now(dt.UTC))
    response = Mock()
    response.json.return_value = {
        "display_name": "Synthetic address",
        "address": {"road": "Synthetic"},
    }
    api.session.get = Mock(return_value=response)
    for vehicle in (first, second, first, second):
        api.update_geocoded_location(token, vehicle, True)
    assert api.session.get.call_count == 2
    call = api.session.get.call_args_list[0]
    assert call.args == ("https://nominatim.openstreetmap.org/reverse",)
    assert call.kwargs["params"]["email"] == token.username
    assert call.kwargs["params"]["lat"] == 0
    assert "Hyundai-Bluelink-JP" in call.kwargs["headers"]["User-Agent"]
    assert "email" not in call.args[0]
    response.raise_for_status.assert_called()


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("PRIVATE"),
        requests.ConnectionError("PRIVATE"),
        requests.HTTPError("PRIVATE"),
        ValueError("PRIVATE"),
    ],
)
def test_geocoder_failure_keeps_address_and_cools_down(api, failure, monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(
        "custom_components.hyundai_jp._api.client.time.monotonic", lambda: clock[0]
    )
    vehicle = Vehicle(id="synthetic")
    vehicle.location = (35.0, 139.0, dt.datetime.now(dt.UTC))
    vehicle.geocode = ("Prior address", "Prior details")
    api.session.get = Mock(side_effect=failure)
    token = Token(username="synthetic", password="synthetic")
    api.update_geocoded_location(token, vehicle, False)
    api.update_geocoded_location(token, vehicle, False)
    assert vehicle.geocode == ("Prior address", "Prior details")
    api.session.get.assert_called_once()
    assert "email" not in api.session.get.call_args.kwargs["params"]
    assert "PRIVATE" not in repr(api.get_local_diagnostics())
    clock[0] += 901
    api.update_geocoded_location(token, vehicle, False)
    assert api.session.get.call_count == 2
