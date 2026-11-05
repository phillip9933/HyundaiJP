"""Schemas and compatibility descriptors follow the running HA API."""

from homeassistant.components.climate import ClimateEntity
from homeassistant.helpers import config_validation as cv
import pytest

from custom_components.hyundai_jp import config_flow
from custom_components.hyundai_jp.climate import HyundaiBluelinkCarClimateControlSwitch
from custom_components.hyundai_jp.ha_validation import Schema, validation
from custom_components.hyundai_jp.service_schemas import SERVICE_SCHEMAS


def test_all_config_and_service_schemas_use_the_hosts_actual_engine():
    expected = type(cv.PLATFORM_SCHEMA)
    assert Schema is validation.Schema is expected
    schemas = (
        config_flow.STEP_RECONFIGURE_DATA_SCHEMA,
        config_flow.STEP_PIN_ONLY_SCHEMA,
        config_flow.STEP_CREDENTIALS_DATA_SCHEMA,
        config_flow.OPTIONS_SCHEMA,
        *SERVICE_SCHEMAS.values(),
    )
    assert all(isinstance(schema, expected) for schema in schemas)


@pytest.mark.parametrize("value", ["not-a-bool", {}, []])
def test_host_and_custom_validation_failures_have_the_same_error_hierarchy(value):
    with pytest.raises(validation.Invalid):
        SERVICE_SCHEMAS["start_climate"]({"climate": value})
    with pytest.raises(validation.Invalid):
        SERVICE_SCHEMAS["set_charging_current"]({"level": value})


def test_current_ha_does_not_override_its_final_temperature_properties():
    entity_type = HyundaiBluelinkCarClimateControlSwitch
    current = hasattr(ClimateEntity, "native_temperature_unit")
    for legacy, native in (
        ("temperature_unit", "native_temperature_unit"),
        ("current_temperature", "native_current_temperature"),
        ("target_temperature", "native_target_temperature"),
    ):
        if current:
            assert legacy not in vars(entity_type)
        else:
            assert vars(entity_type)[legacy] is vars(entity_type)[native]
