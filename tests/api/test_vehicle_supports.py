"""Tests for the vehicle.supports.<item> capability model."""

import dataclasses

import pytest

from custom_components.hyundai_jp._api import VehicleSupports
from custom_components.hyundai_jp._api.ApiImpl import ApiImpl
from custom_components.hyundai_jp._api.ApiImplType1 import ApiImplType1
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
    for api_cls in (
        ApiImpl,
        ApiImplType1,
    ):
        flat = [name for name in dir(api_cls) if "supports" in name]
        assert flat == [], api_cls.__name__


@pytest.mark.parametrize(
    ("alias", "item"),
    [
        ("supports_window_control", "window_control"),
        ("supports_valet_mode", "valet_mode"),
        ("supports_svm", "svm"),
    ],
)
def test_deprecated_flat_alias_reads_and_writes_supports(alias, item):
    vehicle = Vehicle(id="test-id")
    supports = vehicle.supports
    with pytest.deprecated_call():
        setattr(vehicle, alias, True)
    assert vehicle.supports is supports
    assert getattr(vehicle.supports, item) is True
    with pytest.deprecated_call():
        assert getattr(vehicle, alias) is True
