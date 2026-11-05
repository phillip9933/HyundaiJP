"""Small shared validators for Japan control identities and numeric values."""

from functools import lru_cache
import math
import re
from typing import TypeGuard

from .exceptions import InvalidAPIResponseError

CLIMATE_DURATION_MIN = 1
CLIMATE_DURATION_MAX = 10


def vehicle_identifier(value: object) -> str:
    """Validate before a backend identifier is interpolated into a request path."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
    return value


def is_number(value: object) -> TypeGuard[int | float]:
    """Python booleans are integers but never numeric vehicle settings."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_finite_number(value: object) -> TypeGuard[int | float]:
    """Accept the same int/float inputs as controls, excluding booleans and NaN."""
    if not is_number(value):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def is_integer(value: object) -> TypeGuard[int]:
    """Retain supported integer enum/subclass inputs while excluding booleans."""
    return isinstance(value, int) and not isinstance(value, bool)


@lru_cache(maxsize=8)
def temperature_table(
    scale: tuple[int, int, float] | None,
) -> tuple[float | None, ...]:
    """Build the immutable legacy code table once per established profile scale.

    The table retains all 64 code positions, including None for LO/HI/unknown
    codes. It holds no account or vehicle data and can be reused safely.
    """
    if scale is None:
        return (None,) * 0x40
    first, last, minimum = scale
    return tuple(
        minimum + (code - first) * 0.5 if first <= code <= last else None
        for code in range(0x40)
    )
