"""Charge and V2L controls retain their bounds when using HA's descriptor support."""

from unittest.mock import MagicMock

import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.number import (
    NUMBER_DESCRIPTIONS,
    HyundaiKiaConnectNumber,
)


@pytest.mark.parametrize("description", NUMBER_DESCRIPTIONS)
def test_number_bounds_and_units_match_supported_controls(description):
    entity = HyundaiKiaConnectNumber(
        MagicMock(), description, Vehicle(id="synthetic-vehicle")
    )
    bounds = (20, 80) if description.key == "ev_v2l_discharge_limit" else (50, 100)
    assert (entity.native_min_value, entity.native_max_value) == bounds
    assert entity.native_step == 10
    assert entity.native_unit_of_measurement == "%"
