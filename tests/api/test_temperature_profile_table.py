"""Shared command validation table must match every legacy profile code."""

import pytest

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.Vehicle import Vehicle


@pytest.mark.parametrize(
    "profile",
    [
        {"hvacTempType": 1},
        {"hvacTempType": 2},
        {"hvacTempType": 4},
        {"hvacTempType": 1, "climateTempRangeOption": 0},
        {"hvacTempType": 4, "climateTempRangeOption": 0},
        {"hvacTempType": 1, "climateTempRangeOption": 1},
        {"hvacTempType": 4, "climateTempRangeOption": 1},
        {"hvacTempType": True},
        {"hvacTempType": 1, "climateTempRangeOption": True},
        {},
    ],
)
def test_shared_temperature_table_matches_all_profile_codes(profile):
    api = HyundaiBluelinkApi()
    vehicle = Vehicle(id="synthetic")
    api._vehicle_profiles[vehicle.id] = {"option": profile}
    try:
        actual = api._temperature_values(vehicle, profile.get("hvacTempType"))
        expected = tuple(
            api._legacy_temperature(
                vehicle,
                {
                    "value": f"{code:02X}H",
                    "unit": 0,
                    "hvacTempType": profile.get("hvacTempType"),
                },
            )
            for code in range(0x40)
        )
        assert actual == expected
    finally:
        api.close()
