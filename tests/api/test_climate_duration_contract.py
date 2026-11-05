"""Service input and the pure Japan payload builder share the app duration limit."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import voluptuous as vol
import yaml

from custom_components.hyundai_jp import services
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.control_validation import (
    CLIMATE_DURATION_MAX,
    CLIMATE_DURATION_MIN,
)
from custom_components.hyundai_jp._api.options import ClimateRequestOptions
from custom_components.hyundai_jp._api.Vehicle import Vehicle
from custom_components.hyundai_jp.service_schemas import SERVICE_SCHEMAS


@pytest.mark.parametrize(
    "duration", range(CLIMATE_DURATION_MIN, CLIMATE_DURATION_MAX + 1)
)
def test_valid_service_duration_builds_same_native_payload_without_requests(duration):
    validated = SERVICE_SCHEMAS["start_climate"]({"duration": str(duration)})
    api = HyundaiBluelinkApi()
    vehicle = Vehicle(id="synthetic")
    api._vehicle_profiles[vehicle.id] = {
        "option": {"hvacTempType": 1, "clmtCtrlOption": 1}
    }
    api.session.request = Mock(side_effect=AssertionError("preflight is local"))
    try:
        body = api.build_climate_payload(
            vehicle, ClimateRequestOptions(set_temp=20, duration=validated["duration"])
        )
        assert body["ignitionDuration"] == duration
        api.session.request.assert_not_called()
    finally:
        api.close()


@pytest.mark.parametrize("duration", [0, 11, 30, True, 1.5])
def test_invalid_service_duration_is_rejected_before_a_handler(duration):
    with pytest.raises(vol.Invalid):
        SERVICE_SCHEMAS["start_climate"]({"duration": duration})


def test_ui_duration_limits_match_the_native_payload_contract():
    definition = yaml.safe_load(
        Path(services.__file__).with_name("services.yaml").read_text()
    )
    selector = definition["start_climate"]["fields"]["duration"]["selector"]["number"]
    assert (selector["min"], selector["max"]) == (
        CLIMATE_DURATION_MIN,
        CLIMATE_DURATION_MAX,
    )


@pytest.mark.parametrize(
    "value",
    [True, False, float("nan"), float("inf"), -float("inf"), 10**1000],
    ids=["true", "false", "nan", "positive-inf", "negative-inf", "overflowing-int"],
)
def test_service_numeric_boundaries_reject_nonfinite_boolean_and_overflow(value):
    with pytest.raises(vol.Invalid):
        SERVICE_SCHEMAS["start_climate"]({"temperature": value})


@pytest.mark.parametrize(
    "value",
    [True, False, float("nan"), float("inf"), -float("inf"), 10**1000],
    ids=["true", "false", "nan", "positive-inf", "negative-inf", "overflowing-int"],
)
def test_api_numeric_boundaries_reject_invalid_setpoint_before_submission(value):
    api = HyundaiBluelinkApi()
    vehicle = Vehicle(id="synthetic")
    api._vehicle_profiles[vehicle.id] = {"option": {"hvacTempType": 1}}
    api._japan_command = Mock(
        side_effect=AssertionError("invalid controls cannot submit")
    )
    try:
        with pytest.raises(ValueError, match="finite"):
            api.start_climate(Mock(), vehicle, ClimateRequestOptions(set_temp=value))
        api._japan_command.assert_not_called()
    finally:
        api.close()
