"""Tests for ev_driving_range parsing in CCS2 (_update_vehicle_properties_ccs2).

PHEV vehicles report a separate electric-only range in ``DTE.EV`` (distinct
from ``DTE.Total``, which includes the ICE range). The CCS2 status parser set
``ev_driving_range`` only for pure EV (where it equals total_driving_range);
PHEV left it None. The fix reads ``Drivetrain.FuelSystem.DTE.EV`` for PHEV.

The retained upstream Sportage PHEV fixture reports 47 km electric-only range
and 600 km total. These cases exercise the shared CCS2 wire format as ICE, EV
and PHEV; they do not claim vehicle testing in Japan. ICE has no EV-only range.
"""

from custom_components.hyundai_jp._api.const import ENGINE_TYPES
from custom_components.hyundai_jp._api.Vehicle import Vehicle
from tests.api.fixture_helpers import load_fixture

SPORTAGE_PHEV_FIXTURE = "ccs2_sportage_phev_2027_ccs2.json"


def _parse(api, engine_type) -> Vehicle:
    vehicle = Vehicle(id="test-id")
    vehicle.engine_type = engine_type
    state = load_fixture(SPORTAGE_PHEV_FIXTURE)
    api._update_vehicle_properties_ccs2(vehicle, state)
    return vehicle


class TestElectricDrivingRange:
    def test_ice_ev_driving_range_stays_none(self, telemetry_parser):
        """ICE/HEV have no EV-only range: ev_driving_range stays None even
        when the status (here a PHEV's) happens to carry a DTE.EV field."""
        vehicle = _parse(telemetry_parser, ENGINE_TYPES.ICE)
        assert vehicle.total_driving_range == 600.0
        assert vehicle.ev_driving_range is None

    def test_ev_ev_driving_range_equals_total(self, telemetry_parser):
        """Pure EV: ev_driving_range equals total_driving_range (unchanged)."""
        vehicle = _parse(telemetry_parser, ENGINE_TYPES.EV)
        assert vehicle.ev_driving_range == 600.0
        assert vehicle.ev_driving_range == vehicle.total_driving_range

    def test_phev_keeps_separate_electric_range(self, telemetry_parser):
        """PHEV electric-only range must not be replaced by combined range."""
        vehicle = _parse(telemetry_parser, ENGINE_TYPES.PHEV)
        assert vehicle.ev_driving_range == 47.0
        assert vehicle.total_driving_range == 600.0
