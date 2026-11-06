"""Pure service mapping preserves partial choices without sharing mutable data."""

import copy
import datetime as dt
from types import MappingProxyType

from custom_components.hyundai_jp import services


def test_schedule_builder_preserves_empty_days_midnight_zero_units_and_false_flags():
    data = {
        "first_departure_enabled": False,
        "first_departure_days": [0, 6],
        "first_departure_time": "00:00",
        "second_departure_days": [],
        "charging_enabled": False,
        "off_peak_start_time": "00:00",
        "off_peak_charge_only_enabled": False,
        "climate_enabled": False,
        "temperature_unit": 0,
        "defrost": False,
    }
    original = copy.deepcopy(data)
    result = services._schedule_options(MappingProxyType(data))
    assert data == original
    assert result.first_departure.enabled is False
    assert result.first_departure.days == [0, 6]
    assert result.first_departure.time == dt.time(0)
    assert result.second_departure.enabled is None
    assert result.second_departure.days == []
    assert result.second_departure.time is None
    assert result.charging_enabled is False
    assert result.off_peak_charge_only_enabled is False
    assert result.climate_enabled is False
    assert result.defrost is False
    assert result.temperature_unit == 0
    assert result.off_peak_start_time == dt.time(0)
    assert result.off_peak_end_time is result.temperature is None
    result.first_departure.days.append(3)
    assert data == original


def test_absent_or_explicitly_null_schedule_fields_do_not_create_departure_writes():
    for data in ({}, {"first_departure_enabled": None, "second_departure_days": None}):
        result = services._schedule_options(MappingProxyType(data))
        assert result.first_departure is result.second_departure is None
        assert result.charging_enabled is result.climate_enabled is None
        assert result.temperature is result.defrost is None


def test_climate_builder_does_not_retain_previous_requests_or_invent_unknown_settings():
    data = {"temperature": 22.5, "climate": False, "heating": 0, "flseat": 0}
    before = copy.deepcopy(data)
    first = services._climate_options(MappingProxyType(data))
    assert data == before
    assert first.set_temp == 22.5
    assert first.climate is False
    assert first.heating == first.front_left_seat == 0
    assert first.defrost is first.duration is first.steering_wheel is None

    second = services._climate_options(MappingProxyType({"defrost": None}))
    assert second.set_temp is second.climate is second.heating is None
    assert second.front_left_seat is second.defrost is None


def test_navigation_builder_keeps_valid_zero_coordinates_and_optional_text_defaults():
    data = {"latitude": 0, "longitude": 0, "name": "Synthetic destination"}
    result = services._navigation_pois(MappingProxyType(data))
    assert len(result) == 1
    poi = result[0]
    assert poi.coord.lat == poi.coord.lon == 0.0
    assert poi.name == "Synthetic destination"
    assert poi.addr == poi.zip == poi.place_id == ""
    assert data == {"latitude": 0, "longitude": 0, "name": "Synthetic destination"}
