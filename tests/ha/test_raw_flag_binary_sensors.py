"""Tests for binary sensors backed by raw 0/1 API values."""

import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.binary_sensor import SENSOR_DESCRIPTIONS


@pytest.mark.parametrize("key", ["transmission_condition", "headlamp_status"])
@pytest.mark.parametrize(
    ("raw", "expected"), [(None, None), (0, False), (1, True), (True, True)]
)
def test_raw_flag_is_on(key: str, raw: int | None, expected: bool | None) -> None:
    vehicle = Vehicle(id="v1")
    setattr(vehicle, key, raw)
    is_on = next(d for d in SENSOR_DESCRIPTIONS if d.key == key).is_on
    assert is_on is not None
    assert is_on(vehicle) is expected
