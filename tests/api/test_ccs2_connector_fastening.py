"""ConnectorFastening.State is exposed as an enum, not a plug flag.

The CCS2 parser used by Japan reads the standard wire block. The field follows the connector lock, not the cable
(plugged-but-idle reads 0; issues #1395, #1396), so the plug sensor
stays unknown on this path and only unknown or unexpected raw values
must not raise.
"""

import pytest

from custom_components.hyundai_jp._api.const import CONNECTOR_FASTENING_STATE
from custom_components.hyundai_jp._api.telemetry import JapanTelemetry
from custom_components.hyundai_jp._api.Vehicle import Vehicle
from tests.api.fixture_helpers import load_fixture

STATE_FIXTURE = "ccs2_ev9_2024_ccs2.json"


def _type1_api() -> JapanTelemetry:
    api = JapanTelemetry()
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


@pytest.fixture
def parser_api():
    return _type1_api()


@pytest.fixture
def state():
    return load_fixture(STATE_FIXTURE)


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle(id="test-id")


def test_reported_state_populates_enum_plug_unknown(parser_api, state, vehicle):
    # EV9 fixture: fastening 0; then 1 (charged/locked). The plug sensor
    # stays None in both readings.
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert (
        vehicle.ev_connector_fastening_state is CONNECTOR_FASTENING_STATE.DISCONNECTED
    )
    assert vehicle.ev_battery_is_plugged_in is None

    state["Green"]["ChargingInformation"].update({"ConnectorFastening": {"State": 1}})
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_fastening_state is CONNECTOR_FASTENING_STATE.CONNECTED
    assert vehicle.ev_battery_is_plugged_in is None


def test_unknown_value_does_not_crash(parser_api, state, vehicle):
    # Unexpected value -> None (no truthy junk), parser keeps going.
    state["Green"]["ChargingInformation"].update({"ConnectorFastening": {"State": 5}})
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_fastening_state is None
    assert vehicle.ev_battery_is_plugged_in is None


def test_unreported_block_leaves_unknown(parser_api, state, vehicle):
    state["Green"]["ChargingInformation"].pop("ConnectorFastening")
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_fastening_state is None
    assert vehicle.ev_battery_is_plugged_in is None
