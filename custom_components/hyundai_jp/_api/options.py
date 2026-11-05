"""Typed request values for Japan Bluelink controls."""

from dataclasses import dataclass
import datetime as dt

from .const import SEAT_LOCATION, WINDOW_STATE
from .utils import to_int_enum


@dataclass
class ClimateRequestOptions:
    set_temp: float | None = None
    duration: int | None = None
    defrost: bool | None = None
    climate: bool | None = None
    heating: int | None = None
    front_left_seat: int | None = None
    front_right_seat: int | None = None
    rear_left_seat: int | None = None
    rear_right_seat: int | None = None
    steering_wheel: int | None = None
    side_rear_mirror_heating: int | None = (
        None  # 0/1 rear-mirror heating (GSPA sideRearMirrorHeating)
    )
    temp_unit: int | None = None  # 0=Celsius, 1=Fahrenheit (mapped to the
    # string "C"/"F" the GSPA API expects on the wire)
    hvac_temp_type: int | None = None  # HVAC temp type (GSPA hvacTempType, typically 1)
    driver_seat_location: SEAT_LOCATION | None = None  # drvSeatLoc override


@dataclass
class WindowRequestOptions:
    # Universal, position-based vocabulary: front_left/front_right are the
    # physical front windows (not the seats), back_left/back_right the rear
    # ones. The GSPA window-curtain endpoint's seat-based body keys
    # (drvSeatWindow/psgSeatWindow) are derived from these in the API
    # implementation using drvSeatLoc — callers never touch seat keys.
    back_left: WINDOW_STATE | None = None
    back_right: WINDOW_STATE | None = None
    front_left: WINDOW_STATE | None = None
    front_right: WINDOW_STATE | None = None
    rear_left_curtain: WINDOW_STATE | None = None
    rear_right_curtain: WINDOW_STATE | None = None

    def __post_init__(self) -> None:
        """Convert string/int values to WINDOW_STATE enums."""
        self.back_left = to_int_enum(WINDOW_STATE, self.back_left)
        self.back_right = to_int_enum(WINDOW_STATE, self.back_right)
        self.front_left = to_int_enum(WINDOW_STATE, self.front_left)
        self.front_right = to_int_enum(WINDOW_STATE, self.front_right)
        self.rear_left_curtain = to_int_enum(WINDOW_STATE, self.rear_left_curtain)
        self.rear_right_curtain = to_int_enum(WINDOW_STATE, self.rear_right_curtain)


@dataclass
class ScheduleChargingClimateRequestOptions:
    """Options for scheduled charging and climate writes.

    ``None`` on any field means "leave unchanged": the library resolves it
    from the vehicle's current settings. An explicit value (including
    ``False``) is written to the car. On the flat EV5 path a scope with all
    fields ``None`` skips its endpoint entirely; the combined
    ``/reservation/chargehvac`` path always sends both scopes.
    """

    @dataclass
    class DepartureOptions:
        """A departure slot in the Japan charge/climate schedule."""

        enabled: bool | None = None
        days: list[int] | None = None  # Sun=0, Mon=1, ..., Sat=6
        time: dt.time | None = None

    first_departure: DepartureOptions | None = None
    second_departure: DepartureOptions | None = None
    charging_enabled: bool | None = None
    off_peak_start_time: dt.time | None = None
    off_peak_end_time: dt.time | None = None
    off_peak_charge_only_enabled: bool | None = None
    climate_enabled: bool | None = None
    temperature: float | None = None
    # Accepted for existing HA service calls; Japan profile metadata determines
    # the temperature encoding used on the wire.
    temperature_unit: int | None = None
    defrost: bool | None = None


@dataclass
class POICoord:
    lat: float | None = None
    lon: float | None = None
    alt: int = 0
    type: int = 0


@dataclass
class POIInfo:
    phone: str = ""
    waypoint_id: int = 1
    lang: int = 1
    src: str = "HERE"
    coord: POICoord | None = None
    addr: str = ""
    zip: str = ""
    place_id: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, object]:
        coord = self.coord or POICoord()
        return {
            "phone": self.phone,
            "waypointID": self.waypoint_id,
            "lang": self.lang,
            "src": self.src,
            "coord": {
                "lat": coord.lat,
                "alt": coord.alt,
                "lon": coord.lon,
                "type": coord.type,
            },
            "addr": self.addr,
            "zip": self.zip,
            "placeid": self.place_id,
            "name": self.name,
        }
