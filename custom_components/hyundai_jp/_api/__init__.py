"""Japan-only vehicle API bundled with Hyundai Bluelink JP."""

import json
from pathlib import Path

from .const import WINDOW_STATE
from .options import (
    ClimateRequestOptions,
    POICoord,
    POIInfo,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
)
from .svm import SVMDetails
from .Token import Token
from .Vehicle import Vehicle, VehicleSupports
from .VehicleManager import VehicleManager

__all__ = [
    "WINDOW_STATE",
    "ClimateRequestOptions",
    "POICoord",
    "POIInfo",
    "SVMDetails",
    "ScheduleChargingClimateRequestOptions",
    "Token",
    "Vehicle",
    "VehicleManager",
    "VehicleSupports",
    "WindowRequestOptions",
]

# The integration and bundled API are released as one artifact.
__version__: str = json.loads(
    (Path(__file__).parent.parent / "manifest.json").read_text(encoding="utf-8")
)["version"]
