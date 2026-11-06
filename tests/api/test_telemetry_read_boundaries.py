"""Real cached reads keep unknown telemetry distinct from off and MQTT deltas."""

import copy
import datetime as dt
from unittest.mock import Mock

import pytest

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.Vehicle import Vehicle


@pytest.fixture
def reader():
    api = HyundaiBluelinkApi()
    api.update_vehicle_with_cached_location = Mock()
    api.update_vehicle_with_charge_targets = Mock()
    token = Token(username="synthetic", password="synthetic")

    def read(vehicle, state):
        payload = (
            {"state": {"Vehicle": state}}
            if vehicle.ccu_ccs2_protocol_support
            else {"vehicleStatusInfo": {"vehicleStatus": state}}
        )
        response = Mock(status_code=200)
        response.json.return_value = {
            "retCode": "S",
            "resCode": "0000",
            "resMsg": payload,
        }
        api.session.request = Mock(return_value=response)
        api.update_vehicle_with_cached_state(token, vehicle)

    yield api, read
    api.close()


def ccs2_state(date="20261106010000.100"):
    return {
        "Date": date,
        "Drivetrain": {"FuelSystem": {"DTE": {"Total": 200, "Unit": 1}}},
        "Cabin": {
            "Door": {
                "Row1": {"Driver": {"Lock": 0}, "Passenger": {"Lock": 0}},
                "Row2": {"Left": {"Lock": 0}, "Right": {"Lock": 0}},
            }
        },
    }


FLAGS = (
    "defrost_is_on",
    "steering_wheel_heater_is_on",
    "back_window_heater_is_on",
    "ev_charge_port_door_is_open",
    "tire_pressure_all_warning_is_on",
    "tire_pressure_front_left_warning_is_on",
    "smart_key_battery_warning_is_on",
)


def ccs2_flags(state, value):
    state["Body"] = {
        "Windshield": {
            "Front": {"Defog": {"State": value}},
            "Rear": {"Defog": {"State": value}},
        }
    }
    state["Cabin"]["SteeringWheel"] = {"Heat": {"State": value}}
    state["Chassis"] = {
        "Axle": {
            "Tire": {"PressureLow": value},
            "Row1": {"Left": {"Tire": {"PressureLow": value}}},
        }
    }
    state["Green"] = {"ChargingDoor": {"State": value}}
    state["Electronics"] = {"FOB": {"LowBattery": value}}
    return state


def legacy_state(time="20261106100000", value=None):
    state = {"time": time, "doorLock": True}
    if value is not None:
        state.update(
            {
                "defrost": value,
                "steerWheelHeat": value,
                "sideBackWindowHeat": value,
                "sideMirrorHeat": value,
                "smartKeyBatteryWarning": value,
                "evStatus": {"chargePortDoorOpenStatus": 1 if value else 2},
                "tirePressureLamp": {
                    key: value
                    for key in (
                        "tirePressureLampAll",
                        "tirePressureLampFL",
                        "tirePressureLampFR",
                        "tirePressureLampRL",
                        "tirePressureLampRR",
                    )
                },
            }
        )
    return state


@pytest.mark.parametrize("protocol", [0, 1])
def test_new_full_snapshot_clears_missing_flags_and_keeps_core_timestamp(
    reader, protocol
):
    api, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=protocol)
    if protocol:
        read(vehicle, ccs2_flags(ccs2_state(), 1))
    else:
        read(vehicle, legacy_state(value=1))
    assert all(getattr(vehicle, attr) is True for attr in FLAGS)
    read(
        vehicle,
        ccs2_state("20261106010100.200")
        if protocol
        else legacy_state("20261106100100"),
    )
    assert all(getattr(vehicle, attr) is None for attr in FLAGS)
    assert vehicle.is_locked is True
    assert vehicle.last_updated_at == dt.datetime(
        2026, 11, 6, 1, 1, microsecond=200000 if protocol else 0, tzinfo=dt.UTC
    )
    assert api.session.request.call_count == 1


@pytest.mark.parametrize("protocol", [0, 1])
def test_explicit_off_zero_and_on_survive_sequential_cached_reads(reader, protocol):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=protocol)
    for second, value in enumerate((0, 1, 0)):
        state = (
            ccs2_flags(ccs2_state(f"202611060100{second:02}.100"), value)
            if protocol
            else legacy_state(f"202611061000{second:02}", value)
        )
        read(vehicle, state)
        assert all(getattr(vehicle, attr) is bool(value) for attr in FLAGS)


@pytest.mark.parametrize("value", [None, 3, "0", "bad", {}, []])
def test_unsupported_ccs2_flag_codes_are_unknown_instead_of_truthy(reader, value):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    read(vehicle, ccs2_flags(ccs2_state(), value))
    assert all(getattr(vehicle, attr) is None for attr in FLAGS)
    assert vehicle.is_locked is True


def test_mqtt_delta_retains_flags_absent_from_the_observation(reader):
    api, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    read(vehicle, ccs2_flags(ccs2_state(), 1))
    baseline = copy.deepcopy(vehicle.data)
    session = Mock()
    session.drain_state_updates.return_value = [
        {"Date": "20261106010000.200", "DrivingReady": False}
    ]
    api._mqtt_sessions[vehicle.id] = ((), session)
    assert api.apply_cached_mqtt_state(vehicle)
    assert all(getattr(vehicle, attr) is True for attr in FLAGS)
    assert vehicle.data["Body"] == baseline["Body"]
    assert vehicle.engine_is_running is False
    assert vehicle.last_updated_at.microsecond == 200000


TEMPERATURE_FIELDS = (
    ("Cabin.HVAC.Row1.Driver.Temperature.Value", "air_temperature"),
    ("Cabin.HVAC.OutsideTemperature.Value", "outside_temperature"),
    (
        "Green.BatteryManagement.Temperature.CoolingWaterInlet",
        "ev_battery_water_temperature",
    ),
    ("Green.BatteryManagement.Temperature.Min.Raw", "ev_battery_temperature_min"),
    ("Green.BatteryManagement.Temperature.Max.Raw", "ev_battery_temperature_max"),
    (
        "Green.Reservation.Departure.Schedule1.Climate.Temperature",
        "ev_first_departure_climate_temperature",
    ),
    (
        "Green.Reservation.Departure.Schedule2.Climate.Temperature",
        "ev_second_departure_climate_temperature",
    ),
)


def put(state, path, value):
    for part in path.split(".")[:-1]:
        state = state.setdefault(part, {})
    state[path.rsplit(".", 1)[-1]] = value


def temperature_state(path, value, date="20261106010000.100", unit=0):
    state = ccs2_state(date)
    put(state, path, value)
    if path.endswith(".Value"):
        put(state, path.removesuffix("Value") + "Unit", unit)
    elif path.endswith("Climate.Temperature"):
        put(state, path + "Unit", unit)
    return state


INVALID_NUMBERS = [
    None,
    True,
    False,
    "NaN",
    float("inf"),
    -float("inf"),
    10**1000,
    [],
    {},
    "bad",
]
INVALID_NUMBER_IDS = [
    "missing",
    "true",
    "false",
    "nan",
    "inf",
    "negative-inf",
    "overflow",
    "list",
    "dict",
    "text",
]


@pytest.mark.parametrize("path,attribute", TEMPERATURE_FIELDS)
@pytest.mark.parametrize("value", INVALID_NUMBERS, ids=INVALID_NUMBER_IDS)
def test_invalid_optional_temperature_does_not_discard_new_core_status(
    reader, path, attribute, value
):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    read(vehicle, temperature_state(path, 20))
    assert getattr(vehicle, attribute) == 20
    read(vehicle, temperature_state(path, value, "20261106010000.200"))
    assert getattr(vehicle, attribute) is None
    assert vehicle.is_locked is True
    assert vehicle.total_driving_range == 200
    assert vehicle.last_updated_at == dt.datetime(
        2026, 11, 6, 1, 0, 0, 200000, tzinfo=dt.UTC
    )


@pytest.mark.parametrize("path,attribute", TEMPERATURE_FIELDS)
@pytest.mark.parametrize("value", [0, -5, "0", "21.5"])
def test_valid_optional_temperature_numbers_preserve_zero_and_numeric_strings(
    reader, path, attribute, value
):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    read(vehicle, temperature_state(path, value))
    assert getattr(vehicle, attribute) == float(value)
    assert vehicle.is_locked is True


@pytest.mark.parametrize(
    "path,attribute",
    [TEMPERATURE_FIELDS[0], TEMPERATURE_FIELDS[1], *TEMPERATURE_FIELDS[-2:]],
)
@pytest.mark.parametrize("unit", [2, True, "0", {}, [], float("nan")])
def test_unknown_or_malformed_optional_temperature_unit_is_local_to_reading(
    reader, path, attribute, unit
):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    read(vehicle, temperature_state(path, 20))
    read(vehicle, temperature_state(path, 21, "20261106010000.200", unit))
    assert getattr(vehicle, attribute) is None
    assert vehicle.is_locked is True
    assert vehicle.last_updated_at.microsecond == 200000


def test_off_retains_target_and_mqtt_delta_retains_unrelated_valid_temperatures(reader):
    api, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    state = temperature_state(TEMPERATURE_FIELDS[0][0], 22)
    put(state, "Cabin.HVAC.OutsideTemperature", {"Value": 0, "Unit": 0})
    read(vehicle, state)
    off = copy.deepcopy(state)
    off["Date"] = "20261106010000.200"
    put(off, TEMPERATURE_FIELDS[0][0], "OFF")
    read(vehicle, off)
    assert vehicle.air_temperature == 22
    assert vehicle.outside_temperature == 0
    session = Mock()
    session.drain_state_updates.return_value = [
        {"Date": "20261106010000.300", "DrivingReady": False}
    ]
    api._mqtt_sessions[vehicle.id] = ((), session)
    assert api.apply_cached_mqtt_state(vehicle)
    assert vehicle.air_temperature == 22
    assert vehicle.outside_temperature == 0
    assert vehicle.engine_is_running is False


@pytest.mark.parametrize("unit,expected", [(0, "°C"), (1, "°F")])
def test_reported_valid_temperature_units_and_default_departure_fallback_survive(
    reader, unit, expected
):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    state = temperature_state(TEMPERATURE_FIELDS[0][0], 0, unit=unit)
    put(state, "Cabin.HVAC.OutsideTemperature", {"Value": 0, "Unit": unit})
    put(
        state,
        "Green.Reservation.Departure.Schedule1.Climate",
        {"Temperature": 0, "TemperatureUnit": unit},
    )
    put(state, "Green.Reservation.Departure.Schedule2.Climate", {"Temperature": 0})
    read(vehicle, state)
    assert vehicle.air_temperature == 0
    assert vehicle._air_temperature_unit == expected
    assert vehicle.outside_temperature == 0
    assert vehicle._outside_temperature_unit == expected
    assert vehicle.ev_first_departure_climate_temperature == 0
    assert vehicle.ev_first_departure_climate_temperature_unit == expected
    assert vehicle.ev_second_departure_climate_temperature == 0
    assert vehicle.ev_second_departure_climate_temperature_unit == expected


@pytest.mark.parametrize(
    "value",
    [*INVALID_NUMBERS, -1, 255],
    ids=[*INVALID_NUMBER_IDS, "negative", "sentinel"],
)
def test_malformed_optional_tire_pressure_does_not_discard_core_status(reader, value):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    state = ccs2_state()
    put(state, "Chassis.Axle.Tire.PressureUnit", 2)
    put(state, "Chassis.Axle.Row1.Left.Tire.Pressure", 27)
    read(vehicle, state)
    assert vehicle.tire_pressure_front_left == 2.7
    state["Date"] = "20261106010000.200"
    put(state, "Chassis.Axle.Row1.Left.Tire.Pressure", value)
    put(state, "Chassis.Axle.Row1.Right.Tire.Pressure", 0)
    read(vehicle, state)
    assert vehicle.tire_pressure_front_left is None
    assert vehicle.tire_pressure_front_right == 0
    assert vehicle.is_locked is True
    assert vehicle.last_updated_at.microsecond == 200000


@pytest.mark.parametrize("unit", [None, 3, True, "0", {}, [], float("nan")])
def test_malformed_optional_tire_unit_does_not_discard_core_status(reader, unit):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    state = ccs2_state()
    put(state, "Chassis.Axle.Tire.PressureUnit", unit)
    put(state, "Chassis.Axle.Row1.Left.Tire.Pressure", 27)
    read(vehicle, state)
    assert vehicle.tire_pressure_front_left is None
    assert vehicle.tire_pressure_unit is None
    assert vehicle.is_locked is True


def test_off_sentinel_is_not_interpreted_as_an_on_or_supported_warning(reader):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    read(vehicle, ccs2_flags(ccs2_state(), 2))
    for attribute in FLAGS[:4]:
        assert getattr(vehicle, attribute) is False
    for attribute in FLAGS[4:]:
        assert getattr(vehicle, attribute) is None


def test_malformed_temperature_delta_still_publishes_valid_core_observation(reader):
    api, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    state = temperature_state(TEMPERATURE_FIELDS[0][0], 22)
    put(state, "Cabin.HVAC.OutsideTemperature", {"Value": 0, "Unit": 0})
    read(vehicle, state)
    session = Mock()
    session.drain_state_updates.return_value = [
        {
            "Date": "20261106010000.200",
            "DrivingReady": False,
            "Cabin": {"HVAC": {"OutsideTemperature": {"Value": "NaN", "Unit": []}}},
        }
    ]
    api._mqtt_sessions[vehicle.id] = ((), session)
    assert api.apply_cached_mqtt_state(vehicle)
    assert vehicle.air_temperature == 22
    assert vehicle.outside_temperature is None
    assert vehicle.is_locked is True
    assert vehicle.engine_is_running is False
    assert vehicle.last_updated_at.microsecond == 200000


def test_pressure_scaling_overflow_becomes_unknown_without_discarding_status(reader):
    _, read = reader
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    state = ccs2_state()
    put(state, "Chassis.Axle.Tire.PressureUnit", 1)
    put(state, "Chassis.Axle.Row1.Left.Tire.Pressure", 1e308)
    read(vehicle, state)
    assert vehicle.tire_pressure_front_left is None
    assert vehicle.is_locked is True
    assert vehicle.total_driving_range == 200
