"""Missing climate telemetry remains unknown instead of reporting off."""

from homeassistant.components.climate import HVACAction, HVACMode
import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.climate import HyundaiBluelinkCarClimateControlSwitch
from tests.ha.helpers import make_coordinator


@pytest.mark.parametrize(
    "running,mode,action",
    [
        (None, None, None),
        (False, HVACMode.OFF, HVACAction.OFF),
        (True, HVACMode.AUTO, None),
    ],
)
def test_climate_action_requires_explicit_off_telemetry(running, mode, action):
    vehicle = Vehicle(id="synthetic", air_control_is_on=running)
    entity = HyundaiBluelinkCarClimateControlSwitch(make_coordinator(vehicle), vehicle)
    assert entity.hvac_mode is mode
    assert entity.hvac_action is action
