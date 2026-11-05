"""Hyundai Japan Bluelink adapter.

Ordinary controls report HTTP service acceptance without claiming physical
execution. Fresh-status requests retain correlated MQTT completion checks.
"""

from contextlib import ExitStack
import copy
import datetime as dt
import logging
import math
import re
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.exceptions import JSONDecodeError

from .authentication import JapanAuthClient
from .const import DOMAIN, ENGINE_TYPES, ORDER_STATUS, SEAT_STATUS
from .exceptions import (
    APIError,
    AuthenticationError,
    InvalidAPIResponseError,
    NoDataFound,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)
from .japan_commands import JapanCommandsMixin
from .japan_mqtt import JapanMqttSession, JapanMqttTransportPool
from .japan_svm import JapanSVM, _CaptureDiagnostics
from .svm import SVMDetails
from .telemetry import JapanTelemetry
from .Token import Token
from .utils import get_child_value, parse_datetime
from .Vehicle import DailyDrivingStats, Vehicle

_LOGGER = logging.getLogger(__name__)


class HyundaiBluelinkApi(JapanCommandsMixin, JapanSVM, JapanAuthClient):
    """Japan telemetry, profile-aware controls and explicit acceptance receipts."""

    data_timezone = ZoneInfo("Asia/Tokyo")
    FORCE_REFRESH_TIMEOUT = 120
    # Legacy temperature conversion uses per-vehicle Japan profile metadata.
    temperature_range: tuple[float, ...] = ()

    supported_commands = frozenset(
        {
            "request_svm_capture",
            "lock_action",
            "start_charge",
            "stop_charge",
            "start_climate",
            "stop_climate",
            "set_charge_limits",
            "set_charging_current",
            "set_windows_state",
            "charge_port_action",
            "start_hazard_lights",
            "start_hazard_lights_and_horn",
            "set_vehicle_to_load_discharge_limit",
            "schedule_charging_and_climate",
            "valet_mode_action",
            "set_navigation",
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self._telemetry = JapanTelemetry(self.data_timezone, self.temperature_range)
        self.previous_latitude: float | None = None
        self.previous_longitude: float | None = None
        self._japan_actions: dict[
            str, tuple[str, tuple[str, ...] | None, ORDER_STATUS | None]
        ] = {}
        self._japan_command_diagnostics: dict[str, Any] = {}
        self._japan_diagnostic_action_id: str | None = None
        self._japan_refresh_diagnostics: dict[str, Any] = {}
        self._japan_svm_cache: dict[str, SVMDetails] = {}
        self._japan_svm_diagnostics: _CaptureDiagnostics = {"stage": "idle"}
        self._japan_svm_started: float | None = None
        self._passive_state_diagnostics: dict[str, Any] = {}
        self._vehicle_profiles: dict[str, dict[str, Any]] = {}
        self._history_updated_at: dict[str, float] = {}
        self._location_missing_at: dict[str, float] = {}
        self._charge_target_cache: dict[str, tuple[float, dict[int, int]]] = {}
        self._mqtt_sessions: dict[
            str, tuple[tuple[str | None, ...], JapanMqttSession]
        ] = {}
        self._mqtt_sessions_lock = threading.RLock()
        self._mqtt_closed = False
        self._mqtt_transport_pool = JapanMqttTransportPool()
        self._passive_state_observed_at: dict[str, dt.datetime] = {}

    def _mqtt_for_vehicle(self, token: Token, vehicle: Vehicle) -> JapanMqttSession:
        """Reuse a subscribed Japan session only while its authentication is current."""
        if (
            not isinstance(token.mqtt_registration_uuid, str)
            or not token.mqtt_registration_uuid
        ):
            raise AuthenticationError(
                "Reauthenticate Japan account to register MQTT support"
            )
        self._command_vehicle_id(vehicle)
        identity = (token.access_token, token.device_id, token.mqtt_registration_uuid)
        retired = []
        try:
            with self._mqtt_sessions_lock:
                if self._mqtt_closed:
                    raise APIError("Japan API session is closed")
                for vehicle_id, (saved, previous) in list(self._mqtt_sessions.items()):
                    if saved != identity:
                        retired.append(previous)
                        del self._mqtt_sessions[vehicle_id]
                existing = self._mqtt_sessions.get(vehicle.id)
                if existing is not None and (
                    existing[1].closed is True or existing[1].failed is True
                ):
                    retired.append(existing[1])
                    del self._mqtt_sessions[vehicle.id]
                    existing = None
                if existing is None:
                    helper = JapanMqttSession(
                        token,
                        vehicle.id,
                        token.mqtt_registration_uuid,
                        lambda: self._vehicle_headers(token, vehicle),
                        transport_pool=self._mqtt_transport_pool,
                        vehicle=vehicle,
                    )
                    self._mqtt_sessions[vehicle.id] = (identity, helper)
                else:
                    helper = existing[1]
        finally:
            # Disconnect callbacks may re-enter the manager and factory.
            for previous in retired:
                previous.close()
        return helper

    @staticmethod
    def _ccs2_observation_time(value: object) -> dt.datetime:
        """Read Japan CCS2 UTC dates without discarding fractional seconds."""
        if not isinstance(value, str):
            raise TypeError("Invalid Japan telemetry timestamp")
        if re.fullmatch(r"\d{14}(?:\.\d{1,6})?", value):
            pattern = "%Y%m%d%H%M%S.%f" if "." in value else "%Y%m%d%H%M%S"
            return dt.datetime.strptime(value, pattern).replace(tzinfo=dt.UTC)
        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
            r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?",
            value,
        ):
            timestamp = dt.datetime.fromisoformat(value)
            return timestamp.replace(tzinfo=timestamp.tzinfo or dt.UTC).astimezone(
                dt.UTC
            )
        return dt.datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT").replace(
            tzinfo=dt.UTC
        )

    @classmethod
    def _merge_ccs2_observation(
        cls, baseline: dict[str, Any], patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge a telemetry delta without treating omitted fields as false."""
        merged = copy.deepcopy(baseline)
        for key, value in patch.items():
            if not isinstance(key, str):
                raise TypeError("Invalid Japan telemetry field")
            existing = merged.get(key)
            if isinstance(value, dict):
                if existing is not None and not isinstance(existing, dict):
                    raise ValueError("Invalid Japan telemetry object")
                merged[key] = cls._merge_ccs2_observation(existing or {}, value)
            elif isinstance(existing, dict):
                raise TypeError("Invalid Japan telemetry object")
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Invalid Japan telemetry value")
            elif value is None or isinstance(value, (str, int, float, bool, list)):
                merged[key] = copy.deepcopy(value)
            else:
                raise ValueError("Invalid Japan telemetry value")
        return merged

    def _record_passive_state_result(self, result: str) -> None:
        counters = self._passive_state_diagnostics
        if not counters:
            counters = dict.fromkeys(("applied", "stale", "invalid", "missing"), 0)
            self._passive_state_diagnostics = counters
        counters[result] += 1
        counters["last_result"] = result

    def apply_cached_mqtt_state(self, vehicle: Vehicle) -> bool:
        """Apply newer, already-received telemetry; never log in or make requests.

        A valid stored CCS2 snapshot provides the lineage for a delta. Comparing
        parser outputs preserves unrelated values obtained from optional APIs.
        This observation updates state, not the acceptance/execution receipt.
        """
        if (
            type(vehicle.ccu_ccs2_protocol_support) is not int
            or vehicle.ccu_ccs2_protocol_support <= 0
            or not isinstance(vehicle.id, str)
        ):
            self._record_passive_state_result("missing")
            return False
        with self._mqtt_sessions_lock:
            entry = self._mqtt_sessions.get(vehicle.id)
        if entry is None:
            self._record_passive_state_result("missing")
            return False
        try:
            observations = entry[1].drain_state_updates()
        except AttributeError, IndexError, TypeError, RecursionError:
            self._record_passive_state_result("invalid")
            return False
        if not observations:
            self._record_passive_state_result("missing")
            return False

        def observation_time(observation: object) -> dt.datetime:
            try:
                return self._ccs2_observation_time(
                    observation.get("Date") if isinstance(observation, dict) else None
                )
            except AttributeError, TypeError, ValueError, OverflowError:
                return dt.datetime.max.replace(tzinfo=dt.UTC)

        changed = False
        for observation in sorted(observations, key=observation_time):
            if self._apply_cached_mqtt_observation(vehicle, observation):
                changed = True
        return changed

    def _apply_cached_mqtt_observation(
        self, vehicle: Vehicle, observation: object
    ) -> bool:
        """Apply one immutable snapshot from the bounded local receive queue."""
        try:
            if not isinstance(observation, dict) or not observation:
                self._record_passive_state_result("missing")
                return False
            supported_roots = {
                "Body",
                "Cabin",
                "Chassis",
                "Drivetrain",
                "DrivingReady",
                "Electronics",
                "Green",
                "Location",
                "RemoteControl",
            }
            if not any(key in supported_roots for key in observation):
                raise ValueError("Japan telemetry contains no state observation")
            timestamp = self._ccs2_observation_time(observation.get("Date"))
            baseline = vehicle.data
            if not isinstance(baseline, dict) or "status" in baseline:
                raise ValueError("Japan telemetry has no CCS2 baseline")
            previous = self._ccs2_observation_time(baseline.get("Date"))
            if vehicle.last_updated_at is not None:
                if vehicle.last_updated_at.tzinfo is None:
                    raise ValueError("Japan telemetry baseline has no timezone")
                previous = max(previous, vehicle.last_updated_at)
            if timestamp <= previous:
                self._record_passive_state_result("stale")
                return False
            for key in supported_roots - {"DrivingReady"}:
                if key in observation and not isinstance(observation[key], dict):
                    raise ValueError("Invalid Japan telemetry section")
            if "DrivingReady" in observation:
                ready = observation["DrivingReady"]
                if ready is not None and (
                    type(ready) not in (bool, int) or ready not in (0, 1)
                ):
                    raise ValueError("Invalid Japan ignition observation")
            for path in (
                "Cabin.Door.Row1.Driver.Lock",
                "Cabin.Door.Row1.Passenger.Lock",
                "Cabin.Door.Row2.Left.Lock",
                "Cabin.Door.Row2.Right.Lock",
            ):
                raw_lock = get_child_value(observation, path)
                if raw_lock is not None and (
                    type(raw_lock) is not int or raw_lock not in (0, 1)
                ):
                    raise ValueError("Invalid Japan lock observation")
            state = self._merge_ccs2_observation(baseline, observation)
            reference, candidate = copy.deepcopy(vehicle), copy.deepcopy(vehicle)
            self._update_vehicle_properties_ccs2(reference, copy.deepcopy(baseline))
            self._update_vehicle_properties_ccs2(candidate, state)
            candidate.last_updated_at = timestamp.astimezone(self.data_timezone)
            updates = {
                key: value
                for key, value in candidate.__dict__.items()
                if key not in ("data", "_last_updated_at")
                and value != reference.__dict__.get(key)
            }
            updates["data"] = state
            updates["_last_updated_at"] = candidate._last_updated_at
        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            IndexError,
            OverflowError,
            RecursionError,
        ):
            self._record_passive_state_result("invalid")
            return False
        vehicle.__dict__.update(updates)
        self._passive_state_observed_at[vehicle.id] = timestamp
        self._record_passive_state_result("applied")
        return True

    def get_local_diagnostics(self) -> dict[str, Any]:
        """Return bounded protocol counters without requests or private data."""
        with self._mqtt_sessions_lock:
            sessions = list(self._mqtt_sessions.values())[:10]
        return {
            "mqtt_sessions": [helper.diagnostics for _, helper in sessions],
            "svm_images_cached": len(self._japan_svm_cache),
            "last_command": dict(self._japan_command_diagnostics),
            "last_camera_capture": self.svm_diagnostics,
            "last_refresh": dict(self._japan_refresh_diagnostics),
            **(
                {"passive_state": dict(self._passive_state_diagnostics)}
                if self._passive_state_diagnostics
                else {}
            ),
        }

    def close(self) -> None:
        """Release Japan MQTT and HTTP sessions on integration unload."""
        with self._mqtt_sessions_lock:
            if self._mqtt_closed:
                helpers = []
            else:
                self._mqtt_closed = True
                helpers = [helper for _, helper in self._mqtt_sessions.values()]
                self._mqtt_sessions.clear()
                self._passive_state_observed_at.clear()
        # ExitStack attempts every remaining cleanup even when one fails.
        # Register HTTP first so it is closed after all MQTT helpers.
        with ExitStack() as cleanup:
            # Finishing background work can recreate a pool after the first
            # close. Reclose the existing session without creating resources.
            cleanup.callback(self.session.close)
            for helper in reversed(helpers):
                cleanup.callback(helper.close)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Wait for correlated completion and newer telemetry, never just an ACK."""
        self._japan_refresh_diagnostics = {
            "stage": "authentication",
            "outcome": "pending",
        }
        try:
            self._command_vehicle_id(vehicle)
            headers = self._get_control_headers(token, vehicle)
            self._japan_refresh_diagnostics["stage"] = "mqtt_setup"
            helper = self._mqtt_for_vehicle(token, vehicle)
            with helper.refresh_session():
                self._force_refresh_with_session(token, vehicle, helper, headers)
        except Exception as error:
            category = next(
                (
                    name
                    for kind, name in (
                        (RequestTimeoutError, "timeout"),
                        (AuthenticationError, "authentication"),
                        (RateLimitingError, "rate_limited"),
                        (ServiceTemporaryUnavailable, "service_unavailable"),
                        (InvalidAPIResponseError, "invalid_response"),
                        (APIError, "api_error"),
                    )
                    if isinstance(error, kind)
                ),
                "unexpected_error",
            )
            self._japan_refresh_diagnostics.update(outcome="failed", failure=category)
            raise
        self._japan_refresh_diagnostics.update(stage="complete", outcome="success")

    def _force_refresh_with_session(
        self,
        token: Token,
        vehicle: Vehicle,
        helper: JapanMqttSession,
        headers: dict[str, str],
    ) -> None:
        vehicle_id = self._command_vehicle_id(vehicle)
        # A cached PIN grant can expire while MQTT bootstrap is in progress.
        authorization = self._get_control_headers(token, vehicle)["Authorization"]
        headers = self._vehicle_headers(token, vehicle, helper)
        headers["Authorization"] = authorization
        deadline = time.monotonic() + self.FORCE_REFRESH_TIMEOUT
        started_at = dt.datetime.now(dt.UTC).replace(microsecond=0)
        self._japan_refresh_diagnostics["stage"] = "http_request"
        result = self._request(
            "GET", f"/api/v2/spa/vehicles/{vehicle_id}/ccs2/carstatus", headers=headers
        )
        action_id = result.get("msgId")
        if not isinstance(action_id, str) or not action_id:
            raise InvalidAPIResponseError(
                "Japan refresh tracking identifier is missing"
            )
        helper.track(action_id)
        self._japan_refresh_diagnostics["stage"] = "completion"
        try:
            status = helper.status(
                action_id, timeout=min(120, max(0, deadline - time.monotonic()))
            )
            if status == ORDER_STATUS.FAILED:
                raise APIError("Japan vehicle refresh failed")
            if status != ORDER_STATUS.SUCCESS:
                raise RequestTimeoutError("Japan vehicle refresh was not confirmed")
            self._japan_refresh_diagnostics["stage"] = "telemetry"
            while time.monotonic() < deadline:
                state = helper.latest_state
                candidate = copy.deepcopy(vehicle)
                if isinstance(state, dict):
                    try:
                        timestamp = self._ccs2_observation_time(state.get("Date"))
                        if timestamp >= started_at and (
                            vehicle.last_updated_at is None
                            or timestamp > vehicle.last_updated_at
                        ):
                            self._update_vehicle_properties_ccs2(candidate, state)
                            vehicle.__dict__.update(candidate.__dict__)
                            return
                    except KeyError, TypeError, ValueError, AttributeError:
                        pass
                # Some cars publish their fresh data to storage before MQTT state.
                self.update_vehicle_with_cached_state(token, candidate)
                if (
                    candidate.last_updated_at is not None
                    and candidate.last_updated_at >= started_at
                    and (
                        vehicle.last_updated_at is None
                        or candidate.last_updated_at > vehicle.last_updated_at
                    )
                ):
                    vehicle.__dict__.update(candidate.__dict__)
                    return
                time.sleep(min(5, max(0, deadline - time.monotonic())))
            raise RequestTimeoutError("Japan refresh completed without newer telemetry")
        finally:
            helper.retire(action_id)

    def initialize_vehicle_capabilities(self, vehicle: Vehicle) -> None:
        """Japan options describe each vehicle rather than the entire region."""
        service = self._vehicle_profiles.get(vehicle.id, {}).get("serviceOption", {})
        vehicle.supports.window_control = (
            isinstance(service, dict)
            and type(service.get("windowControlOption")) is int
            and service.get("windowControlOption") == 1
        )
        vehicle.supports.valet_mode = (
            isinstance(service, dict)
            and type(service.get("remoteValetActOption")) is int
            and service.get("remoteValetActOption") == 1
        )
        options = self._vehicle_profiles.get(vehicle.id, {}).get("option", {})
        front_only = (
            isinstance(options, dict)
            and type(options.get("windowSafetyOption2")) is int
            and options.get("windowSafetyOption2") == 4
        )
        vehicle.supports.supported_window_positions = (
            ("front_left", "front_right")
            if front_only
            else ("front_left", "front_right", "back_left", "back_right")
        )
        vehicle.supports.svm = (
            isinstance(options, dict)
            and type(options.get("svmOptionInfo")) is int
            and options.get("svmOptionInfo") == 1
        )
        scale = self._temperature_scale(vehicle)
        vehicle.supports.climate_temperature_range = (
            (scale[2], scale[2] + (scale[1] - scale[0]) * 0.5)
            if scale is not None
            else None
        )

    def _update_vehicle_properties_ccs2(
        self, vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        """Map logical driver/passenger telemetry to physical Japan positions."""
        self._telemetry._update_vehicle_properties_ccs2(vehicle, state)
        if state.get("Date"):
            # Preserve CCS2 precision without the legacy timestamp correction.
            vehicle._last_updated_at = self._ccs2_observation_time(
                state["Date"]
            ).astimezone(self.data_timezone)
        options = self._vehicle_profiles.get(vehicle.id, {}).get("option", {})
        location = options.get("drvSeatLoc") if isinstance(options, dict) else None
        for left, right in (
            ("front_left_seat_status", "front_right_seat_status"),
            ("front_left_door_is_open", "front_right_door_is_open"),
            ("front_left_door_is_locked", "front_right_door_is_locked"),
            ("front_left_window_is_open", "front_right_window_is_open"),
        ):
            if location == "R":
                left_value, right_value = (
                    getattr(vehicle, left),
                    getattr(vehicle, right),
                )
                setattr(vehicle, left, right_value)
                setattr(vehicle, right, left_value)
            elif location != "L":
                setattr(vehicle, left, None)
                setattr(vehicle, right, None)

    def _vehicle_headers(
        self, token: Token, vehicle: Vehicle, helper: JapanMqttSession | None = None
    ) -> dict[str, str]:
        """Add only known protocol and authenticated, prepared broker context."""
        headers = self._headers(token)
        protocol = vehicle.ccu_ccs2_protocol_support
        if protocol is not None:
            if type(protocol) is not int or protocol < 0:
                raise InvalidAPIResponseError(
                    "Japan vehicle status protocol is invalid"
                )
            headers["ccuCCS2ProtocolSupport"] = str(protocol)
        if helper is None:
            with self._mqtt_sessions_lock:
                cached = self._mqtt_sessions.get(vehicle.id)
            identity = (
                token.access_token,
                token.device_id,
                token.mqtt_registration_uuid,
            )
            if cached is not None and cached[0] == identity:
                helper = cached[1]
        context = getattr(helper, "request_context", {})
        if isinstance(context, dict):
            for key in ("clientId", "vehicleId"):
                value = context.get(key)
                if isinstance(value, str) and re.fullmatch(
                    r"[A-Za-z0-9_.:-]{1,256}", value
                ):
                    headers[key] = value
        return headers

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Discover vehicles and retrieve profiles to select the status protocol."""
        result = self._request(
            "GET", "/api/v1/spa/vehicles", headers=self._headers(token)
        )
        entries = self._payload(result).get("vehicles")
        if not isinstance(entries, list):
            raise InvalidAPIResponseError("Japan vehicle list is missing")
        vehicles = []
        engine_types = {
            "GN": ENGINE_TYPES.ICE,
            "EV": ENGINE_TYPES.EV,
            "PHEV": ENGINE_TYPES.PHEV,
            "PE": ENGINE_TYPES.PHEV,
            "HV": ENGINE_TYPES.HEV,
        }
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("vehicleId"), str
            ):
                raise InvalidAPIResponseError("Japan vehicle identifier is missing")
            vehicle_id = entry["vehicleId"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", vehicle_id):
                raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
            result = self._request(
                "GET",
                f"/api/v1/spa/vehicles/{vehicle_id}/profile",
                headers=self._vehicle_headers(token, Vehicle(id=vehicle_id)),
            )
            profiles = self._payload(result).get("vinInfo")
            if not isinstance(profiles, list) or len(profiles) != 1:
                raise InvalidAPIResponseError("Japan vehicle profile is incomplete")
            basic = profiles[0].get("basic") if isinstance(profiles[0], dict) else None
            if not isinstance(basic, dict):
                raise InvalidAPIResponseError("Japan vehicle profile is incomplete")
            protocol = basic.get("ccuCCS2ProtocolSupport")
            if type(protocol) is not int or protocol < 0:
                raise InvalidAPIResponseError(
                    "Japan vehicle status protocol is missing"
                )
            self._vehicle_profiles[vehicle_id] = profiles[0]
            vehicles.append(
                Vehicle(
                    id=vehicle_id,
                    name=entry.get("nickname") or entry.get("vehicleName"),
                    model=basic.get("modelName") or entry.get("vehicleName"),
                    registration_date=entry.get("regDate"),
                    VIN=basic.get("vin"),
                    timezone=self.data_timezone,
                    engine_type=engine_types.get(entry.get("type", "")),
                    ccu_ccs2_protocol_support=protocol,
                )
            )
        for vehicle in vehicles:
            self.initialize_vehicle_capabilities(vehicle)
        return vehicles

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Read stored status, optional parking data, and daily energy totals."""
        protocol = vehicle.ccu_ccs2_protocol_support
        if type(protocol) is not int or protocol < 0:
            raise InvalidAPIResponseError("Japan vehicle status protocol is missing")
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        endpoint = "ccs2/carstatus/latest" if protocol else "status/latest"
        headers = self._vehicle_headers(token, vehicle)
        headers["ccuCCS2ProtocolSupport"] = str(protocol)
        payload = self._payload(
            self._request(
                "GET", f"/api/v1/spa/vehicles/{vehicle.id}/{endpoint}", headers=headers
            )
        )
        try:
            parsed_vehicle = copy.deepcopy(vehicle)
            if protocol:
                state = payload["state"]["Vehicle"]
                if not isinstance(state, dict):
                    raise TypeError
                if not state.get("Date"):
                    raise ValueError
                observed_at = self._ccs2_observation_time(state["Date"])
                passive_at = self._passive_state_observed_at.get(vehicle.id)
                retains_passive_state = (
                    passive_at is not None
                    and isinstance(vehicle.data, dict)
                    and self._ccs2_observation_time(vehicle.data.get("Date"))
                    >= passive_at
                )
                if (
                    vehicle.last_updated_at is not None
                    and observed_at < vehicle.last_updated_at
                ) or (
                    retains_passive_state
                    and passive_at is not None
                    and observed_at <= passive_at
                ):
                    self.apply_cached_mqtt_state(vehicle)
                    return
                self._update_vehicle_properties_ccs2(parsed_vehicle, state)
                parsed_vehicle.last_updated_at = observed_at.astimezone(
                    self.data_timezone
                )
            else:
                info = payload["vehicleStatusInfo"]
                state = {
                    "status": info["vehicleStatus"],
                    "vehicleLocation": info.get("vehicleLocation"),
                }
                if not isinstance(state["status"], dict):
                    raise TypeError
                if not state["status"].get("time"):
                    raise ValueError
                parse_datetime(state["status"]["time"], self.data_timezone)
                temperature = self._legacy_temperature(
                    vehicle, state["status"].get("airTemp")
                )
                state = copy.deepcopy(state)
                state["status"].pop("airTemp", None)
                self._telemetry._update_vehicle_properties(parsed_vehicle, state)
                profile_options = self._vehicle_profiles.get(vehicle.id, {}).get(
                    "option", {}
                )
                seat_location = (
                    profile_options.get("drvSeatLoc")
                    if isinstance(profile_options, dict)
                    else None
                )
                seats = get_child_value(state, "status.seatHeaterVentState")
                if isinstance(seats, dict) and seat_location in ("L", "R"):
                    for physical, logical in (
                        (
                            "front_left_seat_status",
                            "drvSeatHeatState"
                            if seat_location == "L"
                            else "astSeatHeatState",
                        ),
                        (
                            "front_right_seat_status",
                            "astSeatHeatState"
                            if seat_location == "L"
                            else "drvSeatHeatState",
                        ),
                    ):
                        if (
                            getattr(parsed_vehicle, physical) is None
                            and logical in seats
                        ):
                            setattr(
                                parsed_vehicle,
                                physical,
                                SEAT_STATUS.get(seats[logical]),
                            )
                parsed_vehicle.air_temperature = (
                    temperature,
                    "°C" if temperature is not None else None,
                )
        except KeyError, TypeError, ValueError, AttributeError:
            raise InvalidAPIResponseError("Japan cached status is incomplete") from None
        # Supplement only absent parking data and limit history reads to daily.
        # Optional malformed data cannot invalidate otherwise valid status.
        if (
            parsed_vehicle.location_latitude is None
            or parsed_vehicle.location_longitude is None
        ) and time.monotonic() - self._location_missing_at.get(
            vehicle.id, -3600
        ) >= 3600:
            try:
                self.update_vehicle_with_cached_location(token, parsed_vehicle)
            except NoDataFound, InvalidAPIResponseError:
                self._location_missing_at[vehicle.id] = time.monotonic()
        if (
            vehicle.engine_type in (ENGINE_TYPES.EV, ENGINE_TYPES.PHEV)
            and time.monotonic() - self._history_updated_at.get(vehicle.id, -86400)
            >= 86400
        ):
            try:
                self.update_vehicle_with_driving_info(token, parsed_vehicle)
            except NoDataFound, InvalidAPIResponseError:
                pass
            self._history_updated_at[vehicle.id] = time.monotonic()
        if parsed_vehicle.engine_type in (ENGINE_TYPES.EV, ENGINE_TYPES.PHEV) and (
            parsed_vehicle.ev_charge_limits_ac is None
            or parsed_vehicle.ev_charge_limits_dc is None
        ):
            cached_targets = self._charge_target_cache.get(vehicle.id)
            if (
                cached_targets is not None
                and time.monotonic() - cached_targets[0] < 3600
            ):
                targets = cached_targets[1]
                parsed_vehicle.ev_charge_limits_ac = targets.get(1, targets.get(2))
                parsed_vehicle.ev_charge_limits_dc = targets.get(0)
            else:
                try:
                    self.update_vehicle_with_charge_targets(token, parsed_vehicle)
                except NoDataFound, InvalidAPIResponseError:
                    self._charge_target_cache[vehicle.id] = (time.monotonic(), {})
        if protocol:
            # MQTT may arrive during optional cached reads. Apply it to the
            # candidate so those optional fields are retained without rollback.
            self.apply_cached_mqtt_state(parsed_vehicle)
        vehicle.__dict__.update(parsed_vehicle.__dict__)

    def update_vehicle_with_charge_targets(
        self, token: Token, vehicle: Vehicle
    ) -> None:
        """Read stored AC/DC targets without waking the vehicle."""
        vehicle_id = self._command_vehicle_id(vehicle)
        payload = self._payload(
            self._request(
                "GET",
                f"/api/v1/spa/vehicles/{vehicle_id}/charge/target",
                headers=self._vehicle_headers(token, vehicle),
            )
        )
        entries = payload.get("targetSOClist")
        if not isinstance(entries, list):
            raise InvalidAPIResponseError("Japan charge target response is incomplete")
        targets = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise InvalidAPIResponseError(
                    "Japan charge target response is incomplete"
                )
            plug, level = entry.get("plugType"), entry.get("targetSOClevel")
            if (
                type(plug) is not int
                or plug not in (0, 1, 2)
                or plug in targets
                or plug in (1, 2)
                and any(kind in targets for kind in (1, 2))
                or type(level) is not int
                or not 0 <= level <= 100
            ):
                raise InvalidAPIResponseError("Japan charge target response is invalid")
            targets[plug] = level
        self._charge_target_cache[vehicle_id] = (time.monotonic(), targets)
        vehicle.ev_charge_limits_ac = targets.get(1, targets.get(2))
        vehicle.ev_charge_limits_dc = targets.get(0)

    def _legacy_temperature(self, vehicle: Vehicle, air_temp: object) -> float | None:
        """Decode profile-dependent Japan temperature codes, leaving LO/HI unknown."""
        if not isinstance(air_temp, dict) or air_temp.get("unit", 0) != 0:
            return None
        scale = self._temperature_scale(vehicle, air_temp.get("hvacTempType"))
        if scale is None:
            return None
        first_code, last_code, minimum = scale
        value = air_temp.get("value")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}H?", value):
            return None
        code = int(value.removesuffix("H"), 16)
        return (
            minimum + (code - first_code) * 0.5
            if first_code <= code <= last_code
            else None
        )

    def _temperature_scale(
        self, vehicle: Vehicle, hvac_type: object = None
    ) -> tuple[int, int, float] | None:
        """Return only a scale established by profile or reported HVAC type."""
        options = self._vehicle_profiles.get(vehicle.id, {}).get("option", {})
        if not isinstance(options, dict):
            return None
        range_option = options.get("climateTempRangeOption")
        if hvac_type is None:
            hvac_type = options.get("hvacTempType")
        if isinstance(range_option, bool) or isinstance(hvac_type, bool):
            return None
        if range_option == 0 or hvac_type in (1, 2) and range_option not in (0, 1):
            first_code, last_code, minimum = 0x06, 0x1A, 17.0
        elif range_option == 1:
            first_code, last_code, minimum = 0x03, 0x1F, 15.5
        elif hvac_type == 4:
            first_code, last_code, minimum = 0x02, 0x20, 15.0
        else:
            return None
        return first_code, last_code, minimum

    def update_vehicle_with_cached_location(
        self, token: Token, vehicle: Vehicle
    ) -> None:
        """Read the stored parking position without contacting the vehicle."""
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        payload = self._payload(
            self._request(
                "GET",
                f"/api/v1/spa/vehicles/{vehicle.id}/location/park",
                headers=self._vehicle_headers(token, vehicle),
            )
        )
        location = payload.get("gpsDetail", payload)
        try:
            if not isinstance(location, dict):
                raise TypeError
            coord = location["coord"]
            latitude, longitude = coord["lat"], coord["lon"]
            if isinstance(latitude, bool) or isinstance(longitude, bool):
                raise TypeError
            latitude, longitude = float(latitude), float(longitude)
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError
            timestamp = parse_datetime(location["time"], self.data_timezone)
            if timestamp is None:
                raise ValueError
        except KeyError, TypeError, ValueError:
            raise InvalidAPIResponseError(
                "Japan cached location is incomplete"
            ) from None
        vehicle.location = (latitude, longitude, timestamp)

    def update_vehicle_with_driving_info(self, token: Token, vehicle: Vehicle) -> None:
        """Read all-time stored EV energy history (periodTarget 0)."""
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        payload = self._payload(
            self._request(
                "POST",
                f"/api/v1/spa/vehicles/{vehicle.id}/drvhistory",
                headers=self._vehicle_headers(token, vehicle),
                json={"periodTarget": 0},
            )
        )
        entries = payload.get("drivingInfo")
        if not isinstance(entries, list):
            raise InvalidAPIResponseError("Japan driving history is incomplete")
        all_time = next(
            (
                item
                for item in entries
                if isinstance(item, dict) and item.get("drivingPeriod") == 0
            ),
            None,
        )
        if all_time is None:
            raise NoDataFound("Japan all-time driving history is unavailable")

        def metric(key: str) -> float | int | None:
            value = all_time.get(key)
            if value is None:
                return None
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise InvalidAPIResponseError("Japan driving history metric is invalid")
            return value

        consumed, regenerated = metric("totalPwrCsp"), metric("regenPwr")
        daily = payload.get("drivingInfoDetail", [])
        if not isinstance(daily, list):
            raise InvalidAPIResponseError("Japan daily energy history is incomplete")
        daily_stats = []
        for day in daily:
            if not isinstance(day, dict):
                raise InvalidAPIResponseError(
                    "Japan daily energy history is incomplete"
                )
            try:
                day_date = dt.datetime.strptime(day["drivingDate"], "%Y%m%d").replace(
                    tzinfo=self.data_timezone
                )
            except KeyError, TypeError, ValueError:
                raise InvalidAPIResponseError(
                    "Japan daily energy date is invalid"
                ) from None
            for key in (
                "totalPwrCsp",
                "motorPwrCsp",
                "climatePwrCsp",
                "eDPwrCsp",
                "batteryMgPwrCsp",
                "regenPwr",
                "calculativeOdo",
            ):
                value = day.get(key)
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise InvalidAPIResponseError(
                        "Japan daily energy metric is invalid"
                    )
            daily_stats.append(
                DailyDrivingStats(
                    date=day_date,
                    total_consumed=day.get("totalPwrCsp"),
                    engine_consumption=day.get("motorPwrCsp"),
                    climate_consumption=day.get("climatePwrCsp"),
                    onboard_electronics_consumption=day.get("eDPwrCsp"),
                    battery_care_consumption=day.get("batteryMgPwrCsp"),
                    regenerated_energy=day.get("regenPwr"),
                    distance=day.get("calculativeOdo"),
                    distance_unit="km",
                )
            )
        vehicle.total_power_consumed = consumed
        vehicle.total_power_regenerated = regenerated
        vehicle.daily_stats = daily_stats

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
