"""Token.py"""

from dataclasses import asdict, dataclass
import datetime as dt
from typing import Any


@dataclass
class Token:
    """Token"""

    username: str
    password: str
    access_token: str | None = None
    refresh_token: str | None = None
    device_id: str | None = None
    # Access Token expiry:
    valid_until: dt.datetime = dt.datetime.min
    pin: str | None = None
    # Japan PIN verification grant, cached with its precise expiry:
    control_token: str | None = None
    control_token_expiry: float = 0
    # Broker-issued MQTT client id, distinct from the phone registration UUID.
    mqtt_client_id: str | None = None
    # Phone UUID sent to notification registration and Service Hub /device/register.
    mqtt_registration_uuid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert Token to a JSON‑serializable dict."""
        data = asdict(self)

        # Convert datetimes to ISO strings
        data["valid_until"] = self.valid_until.isoformat()

        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Token:
        """Create a Token instance from a dict."""
        # Parse datetimes from ISO strings
        valid_until = data.get("valid_until", dt.datetime.min)
        if isinstance(valid_until, str):
            valid_until = dt.datetime.fromisoformat(valid_until)

        return cls(
            username=data["username"],
            password=data["password"],
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            device_id=data.get("device_id"),
            valid_until=valid_until,
            pin=data.get("pin"),
            control_token=data.get("control_token"),
            control_token_expiry=data.get("control_token_expiry", 0),
            mqtt_client_id=data.get("mqtt_client_id"),
            mqtt_registration_uuid=data.get("mqtt_registration_uuid"),
        )
