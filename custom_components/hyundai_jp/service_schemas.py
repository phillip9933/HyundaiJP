"""Server-side validation for public services; omitted settings stay omitted."""

from collections.abc import Callable
from datetime import time
import math
from typing import Any

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.helpers import config_validation as cv
import voluptuous as vol


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise vol.Invalid("expected text")
    return value


def _device(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise vol.Invalid("select one valid vehicle device")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise vol.Invalid("expected a number")
    try:
        result = float(value)
    except ValueError as err:
        raise vol.Invalid("expected a number") from err
    if not math.isfinite(result):
        raise vol.Invalid("expected a finite number")
    return result


def _integer(value: Any) -> int:
    result = _number(value)
    if not result.is_integer():
        raise vol.Invalid("expected a whole number")
    return int(result)


def _time(value: Any) -> str:
    parsed: time = cv.time(value)
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise vol.Invalid("time must contain hours and minutes only")
    return parsed.isoformat(timespec="minutes")


def _changes(*names: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def validate(data: dict[str, Any]) -> dict[str, Any]:
        if not any(data.get(name) is not None for name in names):
            raise vol.Invalid("provide at least one setting to change")
        return data

    return validate


def _schema(
    fields: dict[Any, Any] | None = None, changes: tuple[str, ...] = ()
) -> vol.Schema:
    schema = vol.Schema({vol.Optional(ATTR_DEVICE_ID): _device, **(fields or {})})
    return vol.Schema(vol.All(schema, _changes(*changes))) if changes else schema


def _optional(validator: Any) -> Any:
    return vol.Any(None, validator)


WINDOW = vol.All(_integer, vol.In((0, 1, 2)))
TEMPERATURE = _number  # Vehicle profile supplies the valid native range/step.
TIME = _optional(_time)
BOOL = _optional(cv.boolean)
DAYS = vol.All(list, [vol.All(_integer, vol.Range(min=0, max=6))])

SERVICE_SCHEMAS = {
    name: _schema()
    for name in (
        "update",
        "force_update",
        "lock",
        "unlock",
        "stop_climate",
        "start_charge",
        "stop_charge",
        "open_charge_port",
        "close_charge_port",
        "start_hazard_lights",
        "start_hazard_lights_and_horn",
        "start_valet_mode",
        "stop_valet_mode",
        "capture_svm_image",
    )
}
SERVICE_SCHEMAS["start_climate"] = _schema(
    {
        vol.Optional("duration"): _optional(
            vol.All(_integer, vol.Range(min=1, max=30))
        ),
        vol.Optional("temperature"): _optional(TEMPERATURE),
        vol.Optional("climate"): BOOL,
        vol.Optional("defrost"): BOOL,
        vol.Optional("heating"): _optional(vol.All(_integer, vol.In((0, 1, 2, 3)))),
        vol.Optional("steering_wheel"): _optional(
            vol.All(_integer, vol.Range(min=0, max=2))
        ),
        **{
            vol.Optional(key): _optional(
                vol.All(_integer, vol.In((0, 1, 3, 4, 5, 6, 7, 8)))
            )
            for key in ("flseat", "frseat", "rlseat", "rrseat")
        },
    }
)
SERVICE_SCHEMAS["set_charge_limits"] = _schema(
    {
        vol.Required(key): vol.All(_integer, vol.In((50, 60, 70, 80, 90, 100)))
        for key in ("ac_limit", "dc_limit")
    }
)
SERVICE_SCHEMAS["set_charging_current"] = _schema(
    {vol.Required("level"): vol.All(_integer, vol.In((1, 2, 3)))}
)
_WINDOWS = ("flwindow", "frwindow", "rlwindow", "rrwindow")
SERVICE_SCHEMAS["set_windows"] = _schema(
    {vol.Optional(key): _optional(WINDOW) for key in _WINDOWS}, _WINDOWS
)
_SCHEDULE = {
    **{
        vol.Optional(key): BOOL
        for key in (
            "first_departure_enabled",
            "second_departure_enabled",
            "charging_enabled",
            "off_peak_charge_only_enabled",
            "climate_enabled",
            "defrost",
        )
    },
    **{
        vol.Optional(key): _optional(DAYS)
        for key in (
            "first_departure_days",
            "second_departure_days",
        )
    },
    **{
        vol.Optional(key): TIME
        for key in (
            "first_departure_time",
            "second_departure_time",
            "off_peak_start_time",
            "off_peak_end_time",
        )
    },
    vol.Optional("temperature"): _optional(TEMPERATURE),
    vol.Optional("temperature_unit"): _optional(vol.All(_integer, vol.In((0, 1)))),
}
SERVICE_SCHEMAS["schedule_charging_and_climate"] = _schema(
    _SCHEDULE,
    tuple(str(key.schema) for key in _SCHEDULE if key.schema != "temperature_unit"),
)
SERVICE_SCHEMAS["set_off_peak_charging"] = _schema(
    {
        vol.Optional("mode"): _optional(vol.In(("off", "time", "target"))),
        vol.Optional("off_peak_start_time"): TIME,
        vol.Optional("off_peak_end_time"): TIME,
    },
    ("mode", "off_peak_start_time", "off_peak_end_time"),
)
SERVICE_SCHEMAS["set_navigation"] = _schema(
    {
        vol.Required("latitude"): vol.All(_number, vol.Range(min=-90, max=90)),
        vol.Required("longitude"): vol.All(_number, vol.Range(min=-180, max=180)),
        vol.Required("name"): vol.All(_text, vol.Length(min=1)),
        **{vol.Optional(key): _text for key in ("address", "zip_code", "place_id")},
    }
)
