"""Optional binary sensors appear only when Japan telemetry reports a value."""

import pytest

from custom_components.hyundai_jp._api import Vehicle

KEYS = (
    "air_cleaning_is_on",
    "auto_cut_battery_prewarning_on",
    "hazard_lights_on",
    "windshield_front_heater_is_on",
)


async def test_not_created_when_not_reported(setup_binary_sensors) -> None:
    entities = await setup_binary_sensors(Vehicle(id="v1"))
    assert not set(KEYS) & set(entities)


@pytest.mark.parametrize("value", [False, True])
async def test_created_and_mapped_when_reported(setup_binary_sensors, value) -> None:
    vehicle = Vehicle(id="v1")
    for key in KEYS:
        setattr(vehicle, key, value)
    entities = await setup_binary_sensors(vehicle)
    for key in KEYS:
        assert entities[key].is_on is value
