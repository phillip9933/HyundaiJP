"""Tests for the vehicle.supports.<item> capability model."""

import dataclasses

from custom_components.hyundai_jp._api import VehicleSupports
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.telemetry import JapanTelemetry
from custom_components.hyundai_jp._api.Vehicle import Vehicle


def test_vehicle_supports_defaults_to_undetermined():
    vehicle = Vehicle(id="test-id")
    assert vehicle.supports == VehicleSupports()
    assert all(
        getattr(vehicle.supports, f.name) is None
        for f in dataclasses.fields(VehicleSupports)
    )


def test_vehicle_supports_not_shared_between_vehicles():
    first, second = Vehicle(id="test-id"), Vehicle(id="test-id")
    first.supports.svm = True
    assert second.supports.svm is None


def test_api_holds_no_supports_attributes():
    """Support lives on vehicle.supports, not on the API."""
    for api_cls in (HyundaiBluelinkApi, JapanTelemetry):
        flat = [name for name in dir(api_cls) if "supports" in name]
        assert flat == [], api_cls.__name__
