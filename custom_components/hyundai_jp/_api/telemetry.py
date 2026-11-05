"""Japan legacy and CCS2 telemetry parsing without network or session state."""

from collections.abc import Sequence
import datetime as dt
import logging
from typing import Any, cast

from .const import (
    CONNECTOR_FASTENING_STATE,
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
    PRESSURE_SCALES,
    SEAT_STATUS,
    TEMPERATURE_UNITS,
    PressureUnit,
)
from .utils import (
    bool_or_none,
    ccs2_reservation_time_or_none,
    flag_or_none,
    get_child_value,
    get_hex_temp_into_index,
    normalize_battery_soc,
    normalize_battery_soh,
    parse_datetime,
    pressure_or_none,
    window_is_open,
)
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class JapanTelemetry:
    """Map Japan status documents to vehicle fields without performing I/O."""

    def __init__(
        self, data_timezone: dt.tzinfo = dt.UTC, temperature_range: Sequence[float] = ()
    ) -> None:
        self.data_timezone = data_timezone
        self.temperature_range = temperature_range

    def _get_time_from_string(self, value: Any, timesection: Any) -> dt.time | None:
        if value is None:
            return None
        try:
            if not str(value).strip() or int(value) == 0:
                return None  # "0000" / "0" / "" = unset EV timer (issue #1206)
            lastTwo = int(value[-2:])
            if lastTwo > 60:
                value = int(value) + 40
            if int(value) > 1260:
                return dt.datetime.strptime(str(value), "%H%M").time()
            d = dt.datetime.strptime(str(value), "%I%M")
            if timesection and timesection > 0:
                d += dt.timedelta(hours=12)
            return d.time()
        except (ValueError, TypeError) as e:  # fmt: skip
            _LOGGER.warning(
                "Could not parse EV timer value %r (timesection=%r): %s",
                value,
                timesection,
                e,
            )
            return None

    def _update_vehicle_properties(
        self, vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        if get_child_value(state, "status.time"):
            vehicle.last_updated_at = parse_datetime(
                get_child_value(state, "status.time"), self.data_timezone
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        if get_child_value(state, "status.odometer.value"):
            vehicle.odometer = (
                get_child_value(state, "status.odometer.value"),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.odometer.unit",
                    )
                ],
            )
        vehicle.car_battery_percentage = normalize_battery_soc(
            get_child_value(state, "status.battery.batSoc")
        )
        vehicle.engine_is_running = get_child_value(state, "status.engine")

        # Converts temp to usable number. Currently only support celsius.
        # "FFH" is the CN sentinel for "no valid temperature" (AC off / not
        # reported) — live-verified on a 2025 Custo.  Its decoded index falls
        # far outside temperature_range, so skip it silently instead of
        # raising IndexError.
        air_temp_value = get_child_value(state, "status.airTemp.value")
        if air_temp_value and air_temp_value != "FFH":
            try:
                tempIndex = cast(int, get_hex_temp_into_index(air_temp_value))
                vehicle.air_temperature = (
                    self.temperature_range[tempIndex],
                    TEMPERATURE_UNITS[
                        get_child_value(
                            state,
                            "status.airTemp.unit",
                        )
                    ],
                )
            except ValueError, IndexError:
                _LOGGER.debug(f"{DOMAIN} - Unparsable airTemp value: {air_temp_value}")
        vehicle.defrost_is_on = get_child_value(state, "status.defrost")
        steer_wheel_heat = get_child_value(state, "status.steerWheelHeat")
        if steer_wheel_heat in [0, 2]:
            vehicle.steering_wheel_heater_is_on = False
        elif steer_wheel_heat == 1:
            vehicle.steering_wheel_heater_is_on = True

        vehicle.back_window_heater_is_on = get_child_value(
            state, "status.sideBackWindowHeat"
        )
        vehicle.side_mirror_heater_is_on = get_child_value(
            state, "status.sideMirrorHeat"
        )
        vehicle.front_left_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.flSeatHeatState")
        )
        vehicle.front_right_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.frSeatHeatState")
        )
        vehicle.rear_left_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.rlSeatHeatState")
        )
        vehicle.rear_right_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.rrSeatHeatState")
        )
        vehicle.is_locked = get_child_value(state, "status.doorLock")
        vehicle.front_left_door_is_open = get_child_value(
            state, "status.doorOpen.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "status.doorOpen.frontRight"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "status.doorOpen.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "status.doorOpen.backRight"
        )
        vehicle.hood_is_open = get_child_value(state, "status.hoodOpen")
        vehicle.front_left_window_is_open = get_child_value(
            state, "status.windowOpen.frontLeft"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state, "status.windowOpen.frontRight"
        )
        vehicle.back_left_window_is_open = get_child_value(
            state, "status.windowOpen.backLeft"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state, "status.windowOpen.backRight"
        )
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampRL")
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampFL")
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampFR")
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampRR")
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampAll")
        )
        vehicle.trunk_is_open = get_child_value(state, "status.trunkOpen")
        vehicle.ev_battery_percentage = get_child_value(
            state, "status.evStatus.batteryStatus"
        )
        vehicle.ev_battery_is_charging = get_child_value(
            state, "status.evStatus.batteryCharge"
        )

        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "status.evStatus.batteryPlugin"
        )

        ev_charge_port_door_is_open = get_child_value(
            state, "status.evStatus.chargePortDoorOpenStatus"
        )

        if ev_charge_port_door_is_open == 1:
            vehicle.ev_charge_port_door_is_open = True
        elif ev_charge_port_door_is_open == 2:
            vehicle.ev_charge_port_door_is_open = False
        if (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            )
            is not None
        ):
            vehicle.total_driving_range = (
                round(
                    float(
                        get_child_value(
                            state,
                            "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
                        )
                    ),
                    1,
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",
                    )
                ],
            )
        if (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            )
            is not None
        ):
            vehicle.ev_driving_range = (
                round(
                    float(
                        get_child_value(
                            state,
                            "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
                        )
                    ),
                    1,
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",
                    )
                ],
            )
        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.atc.value"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.etc1.value"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.etc2.value"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.etc3.value"),
            "m",
        )

        target_soc_list = get_child_value(
            state, "status.evStatus.reservChargeInfos.targetSOClist"
        )
        try:
            vehicle.ev_charge_limits_ac = [
                x["targetSOClevel"] for x in target_soc_list if x["plugType"] == 1
            ][-1]
            vehicle.ev_charge_limits_dc = [
                x["targetSOClevel"] for x in target_soc_list if x["plugType"] == 0
            ][-1]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - SOC Levels couldn't be found. May not be an EV.")
        if (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
            )
            is not None
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.unit",
                    )
                ],
            )
        elif get_child_value(
            state,
            "status.dte.value",
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "status.dte.value",
                ),
                DISTANCE_UNITS[get_child_value(state, "status.dte.unit")],
            )

        vehicle.ev_target_range_charge_AC = (
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )
        vehicle.ev_first_departure_enabled = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservChargeSet",
        )
        vehicle.ev_second_departure_enabled = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservChargeSet",
        )
        vehicle.ev_first_departure_days = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.day",
        )
        vehicle.ev_second_departure_days = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.day",
        )

        vehicle.ev_first_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.timeSection",
            ),
        )

        vehicle.ev_second_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.timeSection",
            ),
        )

        vehicle.ev_off_peak_start_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.timeSection",
            ),
        )

        vehicle.ev_off_peak_end_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.timeSection",
            ),
        )

        # offPeakPowerFlag semantics match the apps' vehicle data model:
        # 1 = considering peak time ("off-peak tariffs prioritised"),
        # 2 = only peak time ("off-peak tariffs only"), 0 = not applied.
        if get_child_value(
            state,
            "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",
        ):
            if (
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",
                )
                == 2
            ):
                vehicle.ev_off_peak_charge_only_enabled = True
            elif (
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",
                )
                == 1
            ):
                vehicle.ev_off_peak_charge_only_enabled = False

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "status.washerFluidStatus"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "status.breakOilStatus"
        )
        vehicle.fuel_level = get_child_value(state, "status.fuelLevel")
        vehicle.fuel_level_is_low = get_child_value(state, "status.lowFuelLight")
        vehicle.air_control_is_on = get_child_value(state, "status.airCtrlOn")
        vehicle.smart_key_battery_warning_is_on = get_child_value(
            state, "status.smartKeyBatteryWarning"
        )

        if get_child_value(state, "vehicleLocation.coord.lat"):
            vehicle.location = (
                get_child_value(state, "vehicleLocation.coord.lat"),
                get_child_value(state, "vehicleLocation.coord.lon"),
                parse_datetime(
                    get_child_value(state, "vehicleLocation.time"), self.data_timezone
                ),
            )
        vehicle.data = state

    def _update_vehicle_properties_ccs2(
        self, vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        if get_child_value(state, "Date"):
            # CCS2 'Date' is always in UTC, convert to the region timezone.
            # Always use a named timezone to ensure DST adjustment.
            # In EU, CCS2 'Offset' is always 1 (CET), but CEST is UTC+0100.
            # In AU, CCS2 'Offset' is 10 (AEST), but AEDT is UTC+1100.
            date = parse_datetime(get_child_value(state, "Date"), dt.UTC)
            vehicle.last_updated_at = (
                date.astimezone(self.data_timezone) if date else None
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        vehicle.odometer = (
            get_child_value(state, "Drivetrain.Odometer"),
            DISTANCE_UNITS[1],
        )
        vehicle.car_battery_percentage = normalize_battery_soc(
            get_child_value(state, "Electronics.Battery.Level"),
            get_child_value(state, "Electronics.Battery.SensorReliability"),
        )

        vehicle.engine_is_running = get_child_value(state, "DrivingReady")

        air_temp = get_child_value(
            state,
            "Cabin.HVAC.Row1.Driver.Temperature.Value",
        )
        unit = get_child_value(
            state,
            "Cabin.HVAC.Row1.Driver.Temperature.Unit",
        )

        if air_temp not in (None, "OFF") and unit in TEMPERATURE_UNITS:
            vehicle.air_temperature = (air_temp, TEMPERATURE_UNITS[unit])
        elif air_temp_unit := TEMPERATURE_UNITS.get(unit):
            # With climate off the setpoint is "OFF" but the unit is still
            # reported; keep it so consumers know the car's unit.
            vehicle._air_temperature_unit = air_temp_unit

        outside_temp = get_child_value(state, "Cabin.HVAC.OutsideTemperature.Value")
        outside_temp_unit = get_child_value(state, "Cabin.HVAC.OutsideTemperature.Unit")
        if outside_temp is not None and outside_temp_unit is not None:
            vehicle.outside_temperature = (
                outside_temp,
                TEMPERATURE_UNITS[outside_temp_unit],
            )

        defrost_is_on = get_child_value(state, "Body.Windshield.Front.Defog.State")
        if defrost_is_on in [0, 2]:
            vehicle.defrost_is_on = False
        elif defrost_is_on == 1:
            vehicle.defrost_is_on = True

        steer_wheel_heat = get_child_value(state, "Cabin.SteeringWheel.Heat.State")
        if steer_wheel_heat in [0, 2]:
            vehicle.steering_wheel_heater_is_on = False
        elif steer_wheel_heat == 1:
            vehicle.steering_wheel_heater_is_on = True

        defrost_rear_is_on = get_child_value(state, "Body.Windshield.Rear.Defog.State")
        if defrost_rear_is_on in [0, 2]:
            vehicle.back_window_heater_is_on = False
        elif defrost_rear_is_on == 1:
            vehicle.back_window_heater_is_on = True

        # TODO: status.sideMirrorHeat

        vehicle.front_left_seat_status = SEAT_STATUS.get(
            get_child_value(state, "Cabin.Seat.Row1.Driver.Climate.State")
        )

        vehicle.front_right_seat_status = SEAT_STATUS.get(
            get_child_value(state, "Cabin.Seat.Row1.Passenger.Climate.State")
        )

        vehicle.rear_left_seat_status = SEAT_STATUS.get(
            get_child_value(state, "Cabin.Seat.Row2.Left.Climate.State")
        )

        vehicle.rear_right_seat_status = SEAT_STATUS.get(
            get_child_value(state, "Cabin.Seat.Row2.Right.Climate.State")
        )

        vehicle.headlamp_status = get_child_value(
            state, "Body.Lights.Front.HeadLamp.SystemWarning"
        )
        vehicle.headlamp_left_low = get_child_value(
            state, "Body.Lights.Front.Left.Low.Warning"
        )
        vehicle.headlamp_right_low = get_child_value(
            state, "Body.Lights.Front.Right.Low.Warning"
        )
        vehicle.headlamp_left_high = get_child_value(
            state, "Body.Lights.Front.Left.High.Warning"
        )
        vehicle.headlamp_right_high = get_child_value(
            state, "Body.Lights.Front.Right.High.Warning"
        )
        vehicle.stop_lamp_left = get_child_value(
            state, "Body.Lights.Rear.Left.StopLamp.Warning"
        )
        vehicle.stop_lamp_right = get_child_value(
            state, "Body.Lights.Rear.Right.StopLamp.Warning"
        )
        vehicle.turn_signal_left_front = get_child_value(
            state, "Body.Lights.Front.Left.TurnSignal.Warning"
        )
        vehicle.turn_signal_right_front = get_child_value(
            state, "Body.Lights.Front.Right.TurnSignal.Warning"
        )
        vehicle.turn_signal_left_rear = get_child_value(
            state, "Body.Lights.Rear.Left.TurnSignal.Warning"
        )
        vehicle.turn_signal_right_rear = get_child_value(
            state, "Body.Lights.Rear.Right.TurnSignal.Warning"
        )

        vehicle.front_left_door_is_open = get_child_value(
            state, "Cabin.Door.Row1.Driver.Open"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "Cabin.Door.Row1.Passenger.Open"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "Cabin.Door.Row2.Left.Open"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "Cabin.Door.Row2.Right.Open"
        )

        vehicle.front_left_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row1.Driver.Lock"))
            if get_child_value(state, "Cabin.Door.Row1.Driver.Lock") is not None
            else None
        )
        vehicle.front_right_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row1.Passenger.Lock"))
            if get_child_value(state, "Cabin.Door.Row1.Passenger.Lock") is not None
            else None
        )
        vehicle.back_left_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row2.Left.Lock"))
            if get_child_value(state, "Cabin.Door.Row2.Left.Lock") is not None
            else None
        )
        vehicle.back_right_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row2.Right.Lock"))
            if get_child_value(state, "Cabin.Door.Row2.Right.Lock") is not None
            else None
        )

        vehicle.is_locked = (
            vehicle.front_left_door_is_locked
            and vehicle.front_right_door_is_locked
            and vehicle.back_left_door_is_locked
            and vehicle.back_right_door_is_locked
        )

        vehicle.hood_is_open = get_child_value(state, "Body.Hood.Open")
        vehicle.front_left_window_is_open = window_is_open(
            state,
            "Cabin.Window.Row1.Driver.Open",
            "Cabin.Window.Row1.Driver.OpenLevel",
        )
        vehicle.front_right_window_is_open = window_is_open(
            state,
            "Cabin.Window.Row1.Passenger.Open",
            "Cabin.Window.Row1.Passenger.OpenLevel",
        )
        vehicle.back_left_window_is_open = window_is_open(
            state,
            "Cabin.Window.Row2.Left.Open",
            "Cabin.Window.Row2.Left.OpenLevel",
        )
        vehicle.back_right_window_is_open = window_is_open(
            state,
            "Cabin.Window.Row2.Right.Open",
            "Cabin.Window.Row2.Right.OpenLevel",
        )
        vehicle.sunroof_is_open = flag_or_none(
            get_child_value(state, "Body.Sunroof.Glass.Open")
        )
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row2.Left.Tire.PressureLow")
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row1.Left.Tire.PressureLow")
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row1.Right.Tire.PressureLow")
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row2.Right.Tire.PressureLow")
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Tire.PressureLow")
        )
        # Tire pressure values (model B: raw is in the car's display unit; the
        # scale depends on PressureUnit). Live-confirmed EU Santa Fe 2026:
        #   bar (PressureUnit.BAR) raw 27 -> 2.7  (x0.1, 0.1-bar steps)
        #   psi (PressureUnit.PSI) raw 38 -> 38   (x1, integer psi)
        #   kPa (PressureUnit.KPA) raw 51 -> 255  (x5, 5-kPa steps)
        # See const.PRESSURE_UNITS / PRESSURE_SCALES. Per-tire *_unit labels are
        # read-only properties on Vehicle deriving from tire_pressure_unit.
        _pu_raw = get_child_value(state, "Chassis.Axle.Tire.PressureUnit")
        if _pu_raw is None:
            vehicle.tire_pressure_unit = None
        else:
            try:
                vehicle.tire_pressure_unit = PressureUnit(_pu_raw)
            except ValueError:
                # PressureUnit outside {0:psi,1:kpa,2:bar}; e.g. 3 on
                # indirect-TPMS vehicles (no direct sensor, kia_uvo #1786) ->
                # the car reports no per-tire pressure, so unavailable is
                # correct. Expected on some vehicles, so debug, not warning.
                _LOGGER.debug(
                    "%s - Tire PressureUnit %r not in {0:psi,1:kpa,2:bar}; "
                    "tire pressure values ignored",
                    DOMAIN,
                    _pu_raw,
                )
                vehicle.tire_pressure_unit = None
        _scale = (
            PRESSURE_SCALES.get(vehicle.tire_pressure_unit)
            if vehicle.tire_pressure_unit is not None
            else None
        )
        # 255 (0xFF) is the TPMS "no reading" sentinel (car off / before
        # driving) -> pressure_or_none returns None so the entity shows
        # unavailable instead of an impossible value. See kia_uvo #1783, #1232.
        _pfl = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row1.Left.Tire.Pressure")
        )
        _pfr = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row1.Right.Tire.Pressure")
        )
        _prl = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row2.Left.Tire.Pressure")
        )
        _prr = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row2.Right.Tire.Pressure")
        )
        vehicle.tire_pressure_front_left = (
            round(_pfl * _scale, 1) if _pfl is not None and _scale is not None else None
        )
        vehicle.tire_pressure_front_right = (
            round(_pfr * _scale, 1) if _pfr is not None and _scale is not None else None
        )
        vehicle.tire_pressure_rear_left = (
            round(_prl * _scale, 1) if _prl is not None and _scale is not None else None
        )
        vehicle.tire_pressure_rear_right = (
            round(_prr * _scale, 1) if _prr is not None and _scale is not None else None
        )
        # Drive mode (e.g. "Eco", "Sport", "Comfort", "Snow", "Smart").
        vehicle.drive_mode = get_child_value(state, "Chassis.DrivingMode.State")
        # Low oil level warning (HEV/ICE). None when unreported (no sensor -> no
        # entity downstream).
        vehicle.oil_level_warning_is_on = bool_or_none(
            get_child_value(
                state, "Drivetrain.InternalCombustionEngine.OilLevelWarning"
            )
        )
        # 12V auxiliary battery fault warning. None when unreported.
        vehicle.battery_auxiliary_fail_warning_is_on = bool_or_none(
            get_child_value(state, "Electronics.Battery.Auxiliary.FailWarning")
        )
        # Power/ignition/sleep — gap vs CA/IN flat format.
        vehicle.accessory_on = bool_or_none(
            get_child_value(state, "Electronics.PowerSupply.Accessory")
        )
        vehicle.ign3 = bool_or_none(
            get_child_value(state, "Electronics.PowerSupply.Ignition3")
        )
        vehicle.sleep_mode_check = bool_or_none(
            get_child_value(state, "RemoteControl.SleepMode")
        )
        vehicle.trunk_is_open = get_child_value(state, "Body.Trunk.Open")

        vehicle.ev_battery_percentage = get_child_value(
            state, "Green.BatteryManagement.BatteryRemain.Ratio"
        )

        vehicle.ev_battery_pack_voltage = get_child_value(
            state, "Green.BatteryManagement.BatteryPackVoltage"
        )
        vehicle.ev_battery_chiller_rpm = get_child_value(
            state, "Green.BatteryManagement.ChillerRPM"
        )

        battery_heating_state = get_child_value(
            state, "Green.BatteryManagement.HeatingState"
        )
        if battery_heating_state is not None:
            vehicle.ev_battery_heating_state = bool(battery_heating_state)

        vehicle.ev_battery_water_temperature = (
            get_child_value(
                state, "Green.BatteryManagement.Temperature.CoolingWaterInlet"
            ),
            TEMPERATURE_UNITS[0],
        )
        vehicle.ev_battery_temperature_min = (
            get_child_value(state, "Green.BatteryManagement.Temperature.Min.Raw"),
            TEMPERATURE_UNITS[0],
        )
        vehicle.ev_battery_temperature_max = (
            get_child_value(state, "Green.BatteryManagement.Temperature.Max.Raw"),
            TEMPERATURE_UNITS[0],
        )

        winter_mode = get_child_value(
            state, "Green.BatteryManagement.WinterModeOperation"
        )

        # EV battery preconditioning toggle.
        # EV (e.g. IONIQ 5) reports Green.BatteryManagement.BatteryPreCondition.Status
        # — a configuration setting (stable across ignition/climate state), not a
        # runtime flag (BatteryConditioning is the runtime flag). HEV (e.g. Santa
        # Fe) reports only WinterModeOperation; "winter mode" is itself the
        # battery preconditioning/winter-heating toggle there, so keep it as the
        # fallback (preserves existing HEV behaviour, no regression).
        # Status enum mapping matches the official app (kia_uvo #1823, dump:
        # Status=2 with preconditioning off in vehicle settings):
        #   0 / 2 / 6 = off, 3 / 4 = on.
        # Pre-#1205 this sensor aliased WinterModeOperation, which is absent on
        # EVs -> the sensor never appeared (see API #1187).
        battery_precondition_status = get_child_value(
            state, "Green.BatteryManagement.BatteryPreCondition.Status"
        )
        if battery_precondition_status is not None:
            vehicle.ev_battery_precondition_enabled = battery_precondition_status in (
                3,
                4,
            )
            # WinterModeOperation is not a user-facing "Winter Mode" toggle on
            # EVs (kia_uvo #1823) — leave ev_battery_winter_mode unset (None).
        elif winter_mode is not None:
            vehicle.ev_battery_precondition_enabled = bool(winter_mode)
            vehicle.ev_battery_winter_mode = bool(winter_mode)

        if get_child_value(state, "Green.Electric.SmartGrid.RealTimePower") is not None:
            vehicle.ev_charging_power = get_child_value(
                state, "Green.Electric.SmartGrid.RealTimePower"
            )
        vehicle.ev_battery_remain = get_child_value(
            state, "Green.BatteryManagement.BatteryRemain.Value"
        )
        vehicle.ev_battery_capacity = get_child_value(
            state, "Green.BatteryManagement.BatteryCapacity.Value"
        )
        if battery_soh := normalize_battery_soh(
            get_child_value(state, "Green.BatteryManagement.SoH.Ratio")
        ):
            vehicle.ev_battery_soh_percentage = battery_soh
        # ConnectorFastening.State follows the connector LOCK, not the
        # cable: plugged-but-idle reads 0, a released lock after a charge
        # also reads 0 (0 ambiguous; live: Kia EV6 CCI #1495, legacy
        # kia_uvo#1837 "unplugged when charging stopped"). The plug sensor
        # therefore stays unknown on this path; the enum field carries
        # the reported state.
        vehicle.ev_battery_is_plugged_in = None
        fastening_raw = get_child_value(
            state, "Green.ChargingInformation.ConnectorFastening.State"
        )
        vehicle.ev_connector_fastening_state = None
        if fastening_raw is not None:
            try:
                vehicle.ev_connector_fastening_state = CONNECTOR_FASTENING_STATE(
                    int(fastening_raw)
                )
            except ValueError:
                _LOGGER.debug(
                    "%s - ConnectorFastening.State %r not in "
                    "{0: disconnected, 1: connected}",
                    DOMAIN,
                    fastening_raw,
                )
                vehicle.ev_connector_fastening_state = None
        charging_door_state = get_child_value(state, "Green.ChargingDoor.State")
        if charging_door_state in [0, 2]:
            vehicle.ev_charge_port_door_is_open = False
        elif charging_door_state == 1:
            vehicle.ev_charge_port_door_is_open = True

        vehicle.total_driving_range = (
            float(
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Total",
                )
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Unit",
                )
            ],
        )

        if vehicle.engine_type == ENGINE_TYPES.EV:
            # ev_driving_range is the same as total_driving_range for pure EV
            vehicle.ev_driving_range = (
                vehicle.total_driving_range,
                vehicle.total_driving_range_unit,
            )
        elif vehicle.engine_type == ENGINE_TYPES.PHEV:
            # PHEV reports the electric-only range separately in DTE.EV
            # (DTE.Total includes the ICE range). HEV/ICE have no plug and no
            # EV-only range, so ev_driving_range stays None for them.
            ev_range = get_child_value(state, "Drivetrain.FuelSystem.DTE.EV")
            if ev_range is not None:
                vehicle.ev_driving_range = (
                    float(ev_range),
                    vehicle.total_driving_range_unit,
                )

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "Body.Windshield.Front.WasherFluid.LevelLow"
        )

        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.Charging.RemainTime"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.Quick"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.ICCB"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.Standard"),
            "m",
        )
        vehicle.ev_charge_limits_ac = get_child_value(
            state, "Green.ChargingInformation.TargetSoC.Standard"
        )
        vehicle.ev_charge_limits_dc = get_child_value(
            state, "Green.ChargingInformation.TargetSoC.Quick"
        )
        vehicle.ev_charging_current = get_child_value(
            state, "Green.ChargingInformation.ElectricCurrentLevel.State"
        )
        vehicle.ev_v2l_discharge_limit = get_child_value(
            state, "Green.Electric.SmartGrid.VehicleToLoad.DischargeLimitation.SoC"
        )
        vehicle.ev_target_range_charge_AC = (
            get_child_value(
                state,
                "Green.ChargingInformation.DTE.TargetSoC.Standard",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Unit",
                )
            ],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(
                state,
                "Green.ChargingInformation.DTE.TargetSoC.Quick",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Unit",
                )
            ],
        )
        vehicle.ev_first_departure_enabled = bool(
            get_child_value(state, "Green.Reservation.Departure.Schedule1.Enable")
        )

        vehicle.ev_second_departure_enabled = bool(
            get_child_value(state, "Green.Reservation.Departure.Schedule2.Enable")
        )

        vehicle.ev_power_consumption_battery_cooling = get_child_value(
            state, "Green.PowerConsumption.Moment.BatteryCooling"
        )

        vehicle.ev_power_consumption_battery_heater = get_child_value(
            state, "Green.PowerConsumption.Moment.BatteryHeater"
        )
        vehicle.ev_power_consumption_air_conditioning = get_child_value(
            state, "Green.PowerConsumption.Moment.ClimateAirConditioning"
        )

        # Green.Reservation.OffPeakTime — off-peak charging window + priority mode.
        # Mode 0 = off, 2 = target-priority (off-peak tariffs prioritized),
        # 3 = time-priority (charge only during off-peak). 1 is reserved/unused.
        # When the block is absent (HEV / unconfigured EV) all fields stay None —
        # do NOT synthesise dt.time(0,0), which would create phantom time
        # entities downstream and collide with a legitimate midnight window.
        # The 31:70 "unconfigured" sentinel (#1269) is handled silently by
        # ccs2_reservation_time_or_none.
        off_peak = get_child_value(state, "Green.Reservation.OffPeakTime")
        if off_peak:
            try:
                vehicle.ev_off_peak_start_time = ccs2_reservation_time_or_none(
                    off_peak.get("StartHour"), off_peak.get("StartMin")
                )
                vehicle.ev_off_peak_end_time = ccs2_reservation_time_or_none(
                    off_peak.get("EndHour"), off_peak.get("EndMin")
                )
            except TypeError, ValueError:
                _LOGGER.warning("%s - CCS2 OffPeakTime malformed: %s", DOMAIN, off_peak)
                vehicle.ev_off_peak_start_time = None
                vehicle.ev_off_peak_end_time = None

            mode = off_peak.get("Mode")
            if mode == 0:
                vehicle.ev_schedule_charge_enabled = False
                vehicle.ev_off_peak_charge_only_enabled = None
            elif mode == 2:
                vehicle.ev_schedule_charge_enabled = True
                vehicle.ev_off_peak_charge_only_enabled = False
            elif mode == 3:
                vehicle.ev_schedule_charge_enabled = True
                vehicle.ev_off_peak_charge_only_enabled = True
            elif mode is not None:
                _LOGGER.warning("%s - unknown CCS2 OffPeakTime.Mode: %s", DOMAIN, mode)
        # off_peak is None/falsy -> no OffPeakTime block (HEV/unconfigured) ->
        # ev_off_peak_* and ev_schedule_charge_enabled stay None (None default).

        # Departure schedules — same None-when-absent rule. A missing or
        # stub Schedule block (e.g. EV9 ships Schedule1 = {"Enable": False}
        # with no Hour/Min) must yield None time/days, not a synthesised
        # dt.time(0,0). ev_*_departure_enabled is set earlier from the
        # Enable field and is unaffected. Guard on Hour presence so a
        # disabled-but-remembered schedule (EV6: Enable=0 + Hour present)
        # still parses, while an unconfigured stub (EV9: no Hour) stays None.
        # The 31:70 sentinel can also appear here (ccNC EVs, #1269) and is
        # handled silently by ccs2_reservation_time_or_none.
        schedule1 = get_child_value(state, "Green.Reservation.Departure.Schedule1")
        schedule2 = get_child_value(state, "Green.Reservation.Departure.Schedule2")
        if schedule1 and "Hour" in schedule1:
            try:
                vehicle.ev_first_departure_time = ccs2_reservation_time_or_none(
                    schedule1.get("Hour"), schedule1.get("Min")
                )
            except TypeError, ValueError:
                _LOGGER.warning("%s - CCS2 Schedule1 time malformed", DOMAIN)
            vehicle.ev_first_departure_days = [
                i
                for i, k in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
                if schedule1.get(k) == 1
            ] or None
        if schedule2 and "Hour" in schedule2:
            try:
                vehicle.ev_second_departure_time = ccs2_reservation_time_or_none(
                    schedule2.get("Hour"), schedule2.get("Min")
                )
            except TypeError, ValueError:
                _LOGGER.warning("%s - CCS2 Schedule2 time malformed", DOMAIN)
            vehicle.ev_second_departure_days = [
                i
                for i, k in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
                if schedule2.get(k) == 1
            ] or None

        # Departure climate: Green.Reservation.Departure.Climate (Schedule 1)
        # and Green.Reservation.Departure.Schedule2.Climate
        climate1 = get_child_value(
            state, "Green.Reservation.Departure.Schedule1.Climate"
        ) or get_child_value(state, "Green.Reservation.Departure.Climate")
        climate2 = get_child_value(
            state, "Green.Reservation.Departure.Schedule2.Climate"
        )
        default_temp_unit = (
            climate1.get("TemperatureUnit", 0)
            if climate1 and climate1.get("TemperatureUnit") is not None
            else 0
        )

        def _parse_departure_climate(
            climate: dict[str, Any],
            schedule_activation: object = None,
        ) -> tuple[bool | None, bool | None, tuple[float, str | None] | None]:
            activation = (
                schedule_activation
                if schedule_activation is not None
                else climate.get("Activation")
            )
            enabled = bool(activation) if activation is not None else None
            defrost_val = climate.get("Defrost")
            defrost = bool(defrost_val) if defrost_val is not None else None
            temp: tuple[float, str | None] | None = None
            temp_val = climate.get("Temperature")
            if temp_val is not None:
                try:
                    temp_float = float(temp_val)
                    unit_int = climate.get("TemperatureUnit")
                    if unit_int is None:
                        unit_int = default_temp_unit
                    unit_str = TEMPERATURE_UNITS.get(unit_int, TEMPERATURE_UNITS[0])
                    temp = (temp_float, unit_str)
                except ValueError, TypeError:
                    pass
            return enabled, defrost, temp

        if climate1:
            enabled, defrost, temp = _parse_departure_climate(climate1)
            if enabled is not None:
                vehicle.ev_first_departure_climate_enabled = enabled
            if defrost is not None:
                vehicle.ev_first_departure_climate_defrost = defrost
            if temp is not None:
                vehicle.ev_first_departure_climate_temperature = temp

        if climate2:
            sched2_activation = get_child_value(
                state, "Green.Reservation.Departure.Schedule2.Activation"
            )
            enabled, defrost, temp = _parse_departure_climate(
                climate2, sched2_activation
            )
            if enabled is not None:
                vehicle.ev_second_departure_climate_enabled = enabled
            if defrost is not None:
                vehicle.ev_second_departure_climate_defrost = defrost
            if temp is not None:
                vehicle.ev_second_departure_climate_temperature = temp

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "Body.Windshield.Front.WasherFluid.LevelLow"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "Chassis.Brake.Fluid.Warning"
        )

        vehicle.fuel_level = get_child_value(state, "Drivetrain.FuelSystem.FuelLevel")
        vehicle.fuel_level_is_low = get_child_value(
            state, "Drivetrain.FuelSystem.LowFuelWarning"
        )
        vehicle.air_control_is_on = get_child_value(
            state, "Cabin.HVAC.Row1.Driver.Blower.SpeedLevel"
        )
        vehicle.smart_key_battery_warning_is_on = bool(
            get_child_value(state, "Electronics.FOB.LowBattery")
        )
        if vehicle._ev_estimated_current_charge_duration is not None:
            if vehicle._ev_estimated_current_charge_duration == 0:
                vehicle.ev_battery_is_charging = False
            elif vehicle._ev_estimated_current_charge_duration > 0:
                vehicle.ev_battery_is_charging = True

        if get_child_value(state, "Location.GeoCoord.Latitude"):
            # Missing Location.TimeStamp must surface as None (HA "unknown")
            # rather than a 2000-01-01 sentinel that renders as "27 years ago".
            # See kia_uvo #1771 sidetask.
            timestamp = get_child_value(state, "Location.TimeStamp")
            if timestamp is not None:
                location_last_updated_at = dt.datetime(
                    year=int(get_child_value(timestamp, "Year")),
                    month=int(get_child_value(timestamp, "Mon")),
                    day=int(get_child_value(timestamp, "Day")),
                    hour=int(get_child_value(timestamp, "Hour")),
                    minute=int(get_child_value(timestamp, "Min")),
                    second=int(get_child_value(timestamp, "Sec")),
                    tzinfo=self.data_timezone,
                )
            else:
                location_last_updated_at = None

            vehicle.location = (
                get_child_value(state, "Location.GeoCoord.Latitude"),
                get_child_value(state, "Location.GeoCoord.Longitude"),
                location_last_updated_at,
            )

        vehicle.data = state
