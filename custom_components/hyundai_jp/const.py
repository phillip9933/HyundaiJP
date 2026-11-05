"""Constants for the HyundaiJP integration."""

from enum import StrEnum

DOMAIN: str = "hyundai_jp"

CONF_BRAND: str = "brand"
CONF_FORCE_REFRESH_INTERVAL: str = "force_refresh"
CONF_NO_FORCE_REFRESH_HOUR_START: str = "no_force_refresh_hour_start"
CONF_NO_FORCE_REFRESH_HOUR_FINISH: str = "no_force_refresh_hour_finish"
CONF_ENABLE_GEOLOCATION_ENTITY: str = "enable_geolocation_entity"
CONF_USE_EMAIL_WITH_GEOCODE_API: str = "use_email_with_geocode_api"
CONF_TOKEN: str = "token"

JAPAN_REGION = 10
HYUNDAI_BRAND = 2

CHARGING_CURRENTS = {1: 100, 2: 90, 3: 60}

DEFAULT_SCAN_INTERVAL: int = 30
DEFAULT_FORCE_REFRESH_INTERVAL: int = 1440
DEFAULT_NO_FORCE_REFRESH_HOUR_START: int = 22
DEFAULT_NO_FORCE_REFRESH_HOUR_FINISH: int = 7
DEFAULT_ENABLE_GEOLOCATION_ENTITY: bool = False
DEFAULT_USE_EMAIL_WITH_GEOCODE_API: bool = False

DYNAMIC_UNIT: str = "dynamic_unit"


class OffPeakChargingMode(StrEnum):
    """Off-peak charging schedule mode.

    Maps to the (charging_enabled, off_peak_charge_only_enabled) pair the API
    expects. Values are the strings exposed in services.yaml so the service
    payload converts directly: ``OffPeakChargingMode(call.data["mode"])``.
    """

    OFF = "off"
    TIME = "time"
    TARGET = "target"
