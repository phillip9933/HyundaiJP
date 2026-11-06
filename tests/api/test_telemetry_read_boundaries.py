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
