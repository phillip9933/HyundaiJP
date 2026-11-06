"""Synthetic legacy Japan reads exercise every retained parser section."""

import copy
import datetime as dt
from unittest.mock import Mock

import pytest

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.const import ENGINE_TYPES
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.Vehicle import Vehicle


def legacy_replay_state(second_read=False):
    def departure(days, hour, section):
        return {
            "reservChargeInfoDetail": {
                "reservChargeSet": True,
                "reservInfo": {
                    "day": days,
                    "time": {"time": hour, "timeSection": section},
                },
            }
        }

    def distance(value):
        return {"value": value, "unit": 1}

    def target(plug, level, value):
        return {
            "plugType": plug,
            "targetSOClevel": level,
            "dte": {"rangeByFuel": {"totalAvailableRange": distance(value)}},
        }

    return {
        "status": {
            "time": "20261106100100" if second_read else "20261106100000",
            "odometer": distance(101 if second_read else 100),
            "battery": {"batSoc": 80},
            "engine": False,
            "airTemp": {"value": "0CH", "unit": 0},
            "defrost": not second_read,
            "steerWheelHeat": 2 if second_read else 1,
            "sideBackWindowHeat": False,
            "sideMirrorHeat": True,
            "seatHeaterVentState": {
                "drvSeatHeatState": 7,
                "astSeatHeatState": 4,
                "rlSeatHeatState": 0,
                "rrSeatHeatState": 8,
            },
            "doorLock": not second_read,
            "doorOpen": {
                "frontLeft": False,
                "frontRight": True,
                "backLeft": False,
                "backRight": False,
            },
            "hoodOpen": False,
            "trunkOpen": True,
            "windowOpen": {
                "frontLeft": False,
                "frontRight": True,
                "backLeft": False,
                "backRight": False,
            },
            "tirePressureLamp": {
                "tirePressureLampRL": 0,
                "tirePressureLampFL": 1,
                "tirePressureLampFR": 0,
                "tirePressureLampRR": 0,
                "tirePressureLampAll": 1,
            },
            "evStatus": {
                "batteryStatus": 65,
                "batteryCharge": False,
                "batteryPlugin": 1,
                "chargePortDoorOpenStatus": 2 if second_read else 1,
                "drvDistance": [
                    {
                        "rangeByFuel": {
                            "totalAvailableRange": distance(
                                0 if second_read else 300.25
                            ),
                            "evModeRange": distance(0 if second_read else 200.25),
                            "gasModeRange": distance(0 if second_read else 100),
                        }
                    }
                ],
                "remainTime2": {
                    "atc": {"value": 0},
                    "etc1": {"value": 10},
                    "etc2": {"value": 20},
                    "etc3": {"value": 30},
                },
                "reservChargeInfos": {
                    "targetSOClist": [target(0, 80, 250), target(1, 90, 280)],
                    "reservChargeInfo": departure([1, 3, 5], "0800", 0),
                    "reserveChargeInfo2": departure([], "0930", 1),
                    "offpeakPowerInfo": {
                        "offPeakPowerTime1": {
                            "starttime": {"time": "1000", "timeSection": 1},
                            "endtime": {"time": "0600", "timeSection": 0},
                        },
                        "offPeakPowerFlag": 1 if second_read else 2,
                    },
                },
            },
            "washerFluidStatus": False,
            "breakOilStatus": True,
            "fuelLevel": 40,
            "lowFuelLight": False,
            "airCtrlOn": not second_read,
            "smartKeyBatteryWarning": False,
        },
        "vehicleLocation": {
            "coord": {"lat": 35.0, "lon": 139.0},
            "time": "20261106100000",
        },
    }


def read_legacy_replay(api, vehicle, state):
    response = Mock(status_code=200)
    response.json.return_value = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "vehicleStatusInfo": {
                "vehicleStatus": copy.deepcopy(state["status"]),
                "vehicleLocation": copy.deepcopy(state["vehicleLocation"]),
            }
        },
    }
    api.session.request = Mock(return_value=response)
    api.update_vehicle_with_cached_state(
        Token(username="synthetic", password="synthetic"), vehicle
    )


@pytest.mark.parametrize("engine_type", list(ENGINE_TYPES))
@pytest.mark.parametrize("seat", ["L", "R"])
def test_legacy_cached_replay_preserves_sections_and_profile_variants(
    engine_type, seat
):
    api = HyundaiBluelinkApi()
    api._vehicle_profiles["synthetic"] = {
        "option": {"hvacTempType": 1, "drvSeatLoc": seat}
    }
    api.update_vehicle_with_driving_info = Mock()
    vehicle = Vehicle(
        id="synthetic", ccu_ccs2_protocol_support=0, engine_type=engine_type
    )
    try:
        for second_read in (False, True):
            read_legacy_replay(api, vehicle, legacy_replay_state(second_read))
            assert vehicle.last_updated_at == dt.datetime(
                2026, 11, 6, 1, int(second_read), tzinfo=dt.UTC
            )
            assert vehicle.odometer == (101 if second_read else 100)
            assert vehicle.car_battery_percentage == 80
            assert vehicle.engine_is_running is False
            assert vehicle.air_temperature == 20
            assert vehicle.defrost_is_on is not second_read
            assert vehicle.steering_wheel_heater_is_on is not second_read
            assert vehicle.side_mirror_heater_is_on is True
            assert vehicle.front_left_seat_status == (
                "Medium Heat" if seat == "L" else "Medium Cool"
            )
            assert vehicle.front_right_seat_status == (
                "Medium Cool" if seat == "L" else "Medium Heat"
            )
            assert vehicle.is_locked is not second_read
            assert vehicle.front_right_door_is_open is True
            assert vehicle.front_left_window_is_open is False
            assert vehicle.front_right_window_is_open is True
            assert vehicle.trunk_is_open is True
            assert vehicle.tire_pressure_front_left_warning_is_on is True
            assert vehicle.tire_pressure_rear_left_warning_is_on is False
            assert vehicle.ev_battery_percentage == 65
            assert vehicle.ev_battery_is_charging is False
            assert vehicle.ev_battery_is_plugged_in == 1
            assert vehicle.ev_charge_port_door_is_open is not second_read
            assert vehicle.total_driving_range == (0 if second_read else 300.2)
            assert vehicle.ev_driving_range == (0 if second_read else 200.2)
            assert vehicle.fuel_driving_range == (0 if second_read else 100)
            assert vehicle.ev_estimated_current_charge_duration == 0
            assert vehicle.ev_estimated_fast_charge_duration == 10
            assert vehicle.ev_charge_limits_ac == 90
            assert vehicle.ev_charge_limits_dc == 80
            assert vehicle.ev_target_range_charge_AC == 280
            assert vehicle.ev_target_range_charge_DC == 250
            assert vehicle.ev_first_departure_days == [1, 3, 5]
            assert vehicle.ev_second_departure_days == []
            assert vehicle.ev_first_departure_time == dt.time(8)
            assert vehicle.ev_second_departure_time == dt.time(21, 30)
            assert vehicle.ev_off_peak_start_time == dt.time(22)
            assert vehicle.ev_off_peak_end_time == dt.time(6)
            assert vehicle.ev_off_peak_charge_only_enabled is not second_read
            assert vehicle.washer_fluid_warning_is_on is False
            assert vehicle.brake_fluid_warning_is_on is True
            assert vehicle.fuel_level == 40
            assert vehicle.air_control_is_on is not second_read
            assert vehicle.smart_key_battery_warning_is_on is False
            assert vehicle.location_latitude == 35
            assert vehicle.location_longitude == 139
            assert (
                vehicle.data["status"]["time"]
                == legacy_replay_state(second_read)["status"]["time"]
            )
    finally:
        api.close()
