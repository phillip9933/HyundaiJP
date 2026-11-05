"""The climate entity's unit and step, including before the car reports a unit."""

from unittest.mock import MagicMock

from homeassistant.const import UnitOfTemperature
import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.climate import HyundaiBluelinkCarClimateControlSwitch


def _entity(
    car_unit: str | None, ha_unit: UnitOfTemperature | None
) -> HyundaiBluelinkCarClimateControlSwitch:
    vehicle = Vehicle(id="v1", name="test", model="test")
    vehicle.air_control_is_on = False
    vehicle.air_temperature = (None, car_unit)
    entity = HyundaiBluelinkCarClimateControlSwitch(MagicMock(), vehicle)
    if ha_unit is not None:
        entity.hass = MagicMock()
        entity.hass.config.units.temperature_unit = ha_unit
    return entity


@pytest.mark.parametrize(
    ("ha_unit", "min_temp", "max_temp"),
    [(UnitOfTemperature.FAHRENHEIT, 14, 30), (UnitOfTemperature.CELSIUS, 14, 30)],
)
def test_unknown_car_unit_remains_celsius_native(ha_unit, min_temp, max_temp) -> None:
    entity = _entity(None, ha_unit)
    assert entity.native_temperature_unit == UnitOfTemperature.CELSIUS
    assert (entity.min_temp, entity.max_temp) == (min_temp, max_temp)


def test_native_unit_is_celsius_even_when_telemetry_is_fahrenheit() -> None:
    entity = _entity(UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS)
    assert entity.native_temperature_unit == UnitOfTemperature.CELSIUS


def test_unknown_car_unit_before_attach_is_celsius() -> None:
    assert _entity(None, None).native_temperature_unit == UnitOfTemperature.CELSIUS


@pytest.mark.parametrize(
    ("car_unit", "ha_unit", "step"),
    [
        (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS, 0.5),
        (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT, 0.5),
        (None, UnitOfTemperature.FAHRENHEIT, 0.5),
        (None, UnitOfTemperature.CELSIUS, 0.5),
    ],
)
def test_step_follows_unit(car_unit, ha_unit, step) -> None:
    assert _entity(car_unit, ha_unit).target_temperature_step == step
