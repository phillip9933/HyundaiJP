"""ApiImpl.py"""

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
import datetime as dt
import logging
from typing import Any

import requests
from requests.exceptions import JSONDecodeError

from .const import (
    CHARGE_PORT_ACTION,
    DOMAIN,
    ORDER_STATUS,
    SEAT_LOCATION,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
)
from .svm import SVMDetails
from .Token import Token
from .utils import get_child_value, to_int_enum
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


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


class ApiImplSession(requests.Session):
    """Shared HTTP session with default timeout and connection pooling.

    Japan requests use bounded connect/read timeouts. Vehicle commands are
    never automatically replayed because a timeout can have an unknown outcome.
    """

    HTTP_CONNECT_TIMEOUT = 10
    HTTP_READ_TIMEOUT = 30

    def request(
        self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any
    ) -> requests.Response:
        kwargs.setdefault(
            "timeout", (self.HTTP_CONNECT_TIMEOUT, self.HTTP_READ_TIMEOUT)
        )
        try:
            return super().request(
                method.decode("ascii") if isinstance(method, bytes) else method,
                url,
                *args,
                **kwargs,
            )
        except requests.exceptions.Timeout as exc:
            from .exceptions import RequestTimeoutError

            raise RequestTimeoutError(str(exc)) from exc


class ApiImpl:
    # None preserves consumers that allow all existing regional commands.
    supported_commands: frozenset[str] | None = None

    data_timezone: dt.tzinfo = dt.UTC
    temperature_range: Sequence[float] | None = None
    previous_latitude: float | None = None
    previous_longitude: float | None = None

    def __init__(self) -> None:
        """Initialize."""

    def close(self) -> None:
        """Release adapter-owned HTTP sessions; safe to repeat."""
        seen: set[int] = set()
        with ExitStack() as cleanup:
            for name in ("session", "_service_hub_session", "_sessions"):
                session = vars(self).get(name)
                if isinstance(session, requests.Session) and id(session) not in seen:
                    seen.add(id(session))
                    cleanup.callback(session.close)

    def login(
        self,
        username: str,
        password: str,
        pin: str | None = None,
    ) -> Token:
        """Sign in to Japan Bluelink and return an authentication token."""
        raise NotImplementedError("login is not implemented for this region")

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        raise NotImplementedError("get_vehicles is not implemented for this region")

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        raise NotImplementedError(
            "update_vehicle_with_cached_state is not implemented for this region"
        )

    def get_local_diagnostics(self) -> dict[str, Any]:
        """Return local protocol diagnostics without I/O or private identifiers."""
        return {}

    def apply_cached_mqtt_state(self, vehicle: Vehicle) -> bool:
        """Apply already-received state without I/O; return whether it changed."""
        return False

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        """Return the status of a previously sent remote command"""
        raise NotImplementedError(
            "check_action_status is not implemented for this region"
        )

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Triggers the system to contact the car and get fresh data"""
        raise NotImplementedError(
            "force_refresh_vehicle_state is not implemented for this region"
        )

    def update_geocoded_location(
        self,
        token: Token,
        vehicle: Vehicle,
        use_email: bool,
    ) -> None:
        if vehicle.location_latitude and vehicle.location_longitude:
            if (
                vehicle.geocode
                and vehicle.location_latitude == self.previous_latitude
                and vehicle.location_longitude == self.previous_longitude
            ):  # previous coordinates are the same, so keep last valid vehicle.geocode
                _LOGGER.debug(f"{DOMAIN} - Keeping last geocode location")
            else:
                email_parameter = ""
                if use_email is True:
                    email_parameter = "&email=" + token.username

                url = (
                    "https://nominatim.openstreetmap.org/reverse?lat="
                    + str(vehicle.location_latitude)
                    + "&lon="
                    + str(vehicle.location_longitude)
                    + "&format=json&addressdetails=1&zoom=18"
                    + email_parameter
                )
                headers = {"user-agent": "curl/7.81.0"}
                response = requests.get(url, headers=headers, timeout=(5, 15))
                try:
                    response = response.json()
                except JSONDecodeError:
                    _LOGGER.warning(f"{DOMAIN} - failed geocode openstreetmap")
                    vehicle.geocode = None
                else:
                    vehicle.geocode = (
                        get_child_value(response, "display_name"),
                        get_child_value(response, "address"),
                    )
                    self.previous_latitude = vehicle.location_latitude
                    self.previous_longitude = vehicle.location_longitude
                    _LOGGER.debug(f"{DOMAIN} - geocode openstreetmap")

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str | None:
        """Lock or unlocks a vehicle.  Returns the tracking ID"""
        raise NotImplementedError("lock_action is not implemented for this region")

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str | None:
        """Starts climate or remote start.  Returns the tracking ID"""
        raise NotImplementedError("start_climate is not implemented for this region")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str | None:
        """Stops climate or remote start.  Returns the tracking ID"""
        raise NotImplementedError("stop_climate is not implemented for this region")

    def start_charge(self, token: Token, vehicle: Vehicle) -> str | None:
        """Starts charge. Returns the tracking ID"""
        raise NotImplementedError("start_charge is not implemented for this region")

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str | None:
        """Stops charge. Returns the tracking ID"""
        raise NotImplementedError("stop_charge is not implemented for this region")

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str | None:
        """Sets charge limits. Returns the tracking ID"""
        raise NotImplementedError(
            "set_charge_limits is not implemented for this region"
        )

    def set_charging_current(self, token: Token, vehicle: Vehicle, level: int) -> str:
        """
        feature only available for some regions.
        Sets charge current level (1=100%, 2=90%, 3=60%). Returns the tracking ID
        """
        raise NotImplementedError(
            "set_charging_current is not implemented for this region"
        )

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Opens or closes a particular window. Returns the tracking ID"""
        raise NotImplementedError(
            "set_windows_state is not implemented for this region"
        )

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        """Opens or closes the charging port of the car. Returns the tracking ID"""
        raise NotImplementedError(
            "charge_port_action is not implemented for this region"
        )

    def schedule_charging_and_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        """
        feature only available for some regions.
        Schedule charging and climate control. Returns the tracking ID
        """
        raise NotImplementedError(
            "schedule_charging_and_climate is not implemented for this region"
        )

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str | None:
        """Turns on the hazard lights for 30 seconds"""
        raise NotImplementedError(
            "start_hazard_lights is not implemented for this region"
        )

    def start_hazard_lights_and_horn(
        self, token: Token, vehicle: Vehicle
    ) -> str | None:
        """Turns on the hazard lights and horn for 30 seconds"""
        raise NotImplementedError(
            "start_hazard_lights_and_horn is not implemented for this region"
        )

    def valet_mode_action(
        self, token: Token, vehicle: Vehicle, action: VALET_MODE_ACTION
    ) -> str:
        """
        feature only available for some regions.
        Activate or Deactivate valet mode. Returns the tracking ID
        """
        raise NotImplementedError(
            "valet_mode_action is not implemented for this region"
        )

    def set_vehicle_to_load_discharge_limit(
        self, token: Token, vehicle: Vehicle, limit: int
    ) -> str:
        """
        feature only available for some regions.
        Set the vehicle to load limit. Returns the tracking ID
        """
        raise NotImplementedError(
            "set_vehicle_to_load_discharge_limit is not implemented for this region"
        )

    def set_navigation(
        self, token: Token, vehicle: Vehicle, poi_list: list[POIInfo]
    ) -> str:
        """Send navigation destinations to the vehicle. Returns the tracking ID."""
        raise NotImplementedError("set_navigation is not implemented for this region")

    def refresh_access_token(self, token: Token) -> Token:
        """Refresh the token using the refresh token"""
        # By default, just call login again, ideally use the refresh token flow
        # Pass the pin explicitly as a keyword to avoid positional
        # argument mis-binding in subclasses that accept different
        # login() signatures (some accept a `token` positional arg).
        return self.login(
            username=token.username, password=token.password, pin=token.pin
        )

    def get_svm_details(self, token: Token, vehicle: Vehicle) -> SVMDetails:
        """Return the latest SVM composite image and metadata."""
        raise NotImplementedError("get_svm_details is not implemented for this region")

    def request_svm_capture(
        self,
        token: Token,
        vehicle: Vehicle,
        acknowledged_warning: bool = False,
    ) -> SVMDetails:
        """Trigger a fresh SVM capture and return the resulting image."""
        raise NotImplementedError(
            "request_svm_capture is not implemented for this region"
        )
