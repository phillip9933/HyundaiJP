"""Japan Service Hub wire contracts and registration orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
import uuid

from .Token import Token
from .Vehicle import Vehicle

TOPIC_PREFIX_RESULT = "service/phone/_/res/"
TOPIC_PREFIX_VSS = "service/phone/_/vss/"  # CarStatus.Status
TOPIC_PREFIX_CONNECTION = "service/phone/_/connection/"  # CarStatus.Connect
TOPIC_PREFIX_LOCATION = "service/phone/_/location/"  # CarStatus.Location

# Protocol IDs — sent as plain strings in the Service Hub body
MQTT_PROTOCOL_ID_VSS = "service.phone.vss"
MQTT_PROTOCOL_ID_CONNECTION = "service.phone.connection"
MQTT_PROTOCOL_ID_RES = "service.phone.res"
MQTT_PROTOCOL_ID_CCU_UPDATE = "statesync.vehicle.ccu.update"


@dataclass
class PushMessage:
    """Parsed Service Hub MQTT delivery — the push-message contract shape."""

    topic_group: str  # "CarStatus", "DeviceRemote", "CarOta", ...
    topic_type: str  # "Status", "Connect", "Res", ...
    vehicle_id: str  # push-level id (topic infix)
    payload: dict[str, Any]  # parsed JSON (header/body as delivered)
    is_status: bool = False


def build_car_status_topics(
    vehicle_id: str, *, result_client_id: str | None = None
) -> list[str]:
    """Build CarStatus topics for a vehicle.

    The default excludes results as required by EU brokers; regions supporting
    result delivery opt in with their registered client id.
    """
    topics = [TOPIC_PREFIX_VSS + vehicle_id]
    if result_client_id is not None:
        topics.append(TOPIC_PREFIX_RESULT + result_client_id)
    topics.append(TOPIC_PREFIX_CONNECTION + vehicle_id)
    return topics


def classify_phone_topic(
    topic: str, *, include_result: bool = False
) -> tuple[str, str, str] | None:
    """Classify a phone-family topic into (group, type, infix).

    ``None`` when the topic is not phone-family (that is regional
    schema; the region classes classify their own topics).
    """
    if include_result and topic.startswith(TOPIC_PREFIX_RESULT):
        return ("CarStatus", "Res", _extract_infix(topic, TOPIC_PREFIX_RESULT))
    if topic.startswith(TOPIC_PREFIX_VSS):
        return ("CarStatus", "Status", _extract_infix(topic, TOPIC_PREFIX_VSS))
    if topic.startswith(TOPIC_PREFIX_CONNECTION):
        return ("CarStatus", "Connect", _extract_infix(topic, TOPIC_PREFIX_CONNECTION))
    if topic.startswith(TOPIC_PREFIX_LOCATION):
        return ("CarStatus", "Location", _extract_infix(topic, TOPIC_PREFIX_LOCATION))
    return None


def _extract_infix(topic: str, prefix: str) -> str:
    """Extract the vehicle/client id infix from a topic after the prefix.

    The infix is the segment between the prefix and the next '/' (or the
    whole remainder for a trailing-slash topic).
    """
    after_prefix = topic[len(prefix) :]
    slash_pos = after_prefix.find("/")
    if slash_pos > 0:
        return after_prefix[:slash_pos]
    if after_prefix.endswith("/"):
        return after_prefix[:-1]
    return after_prefix


def build_service_hub_url_with_params(
    base: str, path: str, params: dict[str, Any] | None
) -> str:
    """Build a Service Hub URL with query params (app-confirmed encoding).

    The server validates parameter values exactly as sent
    (live-probed): '@' is preserved, not percent-encoded.
    """
    full_url = base + path
    if not params:
        return full_url
    safe_chars = "-_.~!$'()*,;=:@/?"
    parts = [
        f"{quote(str(k), safe=safe_chars)}={quote(str(v), safe=safe_chars)}"
        for k, v in params.items()
    ]
    return f"{full_url}?{'&'.join(parts)}"


def parse_service_hub_status_message(
    topic: str, payload: bytes, *, include_result: bool = False
) -> PushMessage | None:
    """Parse bounded object deliveries; only status topics signal vehicle data."""
    classification = classify_phone_topic(topic, include_result=include_result)
    if classification is None or len(payload) > 2 * 1024 * 1024:
        return None
    try:
        data = json.loads(payload)
    except ValueError, UnicodeDecodeError, RecursionError:
        return None
    if not isinstance(data, dict):
        return None
    group, kind, identity = classification
    return PushMessage(group, kind, identity, data, kind == "Status")


class ServiceHubProviderMixin:
    """Service Hub registration used by the Japan MQTT session."""

    if TYPE_CHECKING:
        _service_hub_tid: str | None

        @property
        def _service_hub_brand(self) -> str: ...

        def _service_hub_registered_client_id(self, token: Token) -> str: ...

        def _service_hub_registration_uuid(self, token: Token) -> str: ...

        def _service_hub_request(
            self,
            token: Token,
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None,
            json: dict[str, Any] | None = None,
            allow_not_found: bool = False,
        ) -> dict[str, Any] | None: ...

        def _service_hub_broker_info(self, data: dict[str, Any]) -> dict[str, Any]: ...

        def _service_hub_client_id(self, data: dict[str, Any]) -> str: ...

        def _service_hub_apply_metadata(
            self, data: dict[str, Any], vehicle: Vehicle
        ) -> None: ...

        def _service_hub_vehicle_id_request(
            self, vehicle: Vehicle
        ) -> dict[str, Any]: ...

        def _service_hub_validate_vehicle_identity(self, vehicle: Vehicle) -> None: ...

    def _service_hub_registration_headers(
        self, token: Token, *, client: bool = False
    ) -> dict[str, str]:
        headers = {"tid": self._service_hub_tid or str(uuid.uuid4())}
        if client:
            headers["client-id"] = self._service_hub_registered_client_id(token)
        return headers

    def get_push_broker_info(self, token: Token) -> dict[str, Any] | None:
        data = self._service_hub_request(token, "GET", "/api/v3/servicehub/device/host")
        if data is None:
            return None
        return self._service_hub_broker_info(data)

    def register_push_client(self, token: Token) -> dict[str, Any] | None:
        data = self._service_hub_request(
            token,
            "POST",
            "/api/v3/servicehub/device/register",
            headers=self._service_hub_registration_headers(token),
            json={"unit": "mobile", "uuid": self._service_hub_registration_uuid(token)},
        )
        if data is None:
            return None
        client_id = self._service_hub_client_id(data)
        if not client_id:
            return None
        token.mqtt_client_id = client_id
        return {
            "client_id": client_id,
            "device_id": data.get("deviceId"),
            "username": data.get("username"),
            "password": data.get("password"),
        }

    def _service_hub_protocols(self, vehicle: Vehicle) -> list[str]:
        return [MQTT_PROTOCOL_ID_VSS, MQTT_PROTOCOL_ID_CONNECTION, MQTT_PROTOCOL_ID_RES]

    def register_push_vehicle(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        return self._service_hub_request(
            token,
            "POST",
            "/api/v3/servicehub/device/protocol",
            headers=self._service_hub_registration_headers(token, client=True),
            json={
                "protocols": self._service_hub_protocols(vehicle),
                "protocolId": MQTT_PROTOCOL_ID_CCU_UPDATE,
                "carId": vehicle.id or "",
                "brand": self._service_hub_brand,
            },
        )

    def _service_hub_vehicle_params(self, vehicle: Vehicle) -> dict[str, Any]:
        return {"carId": vehicle.id or "", "brand": self._service_hub_brand}

    def _service_hub_get_metadata(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        vehicle.mqtt_vehicle_id = None
        data = self._service_hub_request(
            token,
            "GET",
            "/api/v3/servicehub/vehicles/metadatalist",
            headers=self._service_hub_registration_headers(token, client=True),
            params=self._service_hub_vehicle_params(vehicle),
            allow_not_found=True,
        )
        if data is not None:
            self._service_hub_apply_metadata(data, vehicle)
        return data

    def _service_hub_get_vehicle_id(self, token: Token, vehicle: Vehicle) -> str | None:
        data = self._service_hub_request(
            token,
            "POST",
            "/api/v3/servicehub/vehicleId",
            headers=self._service_hub_registration_headers(token, client=True),
            **self._service_hub_vehicle_id_request(vehicle),
        )
        if data is None:
            return None
        vehicle_id = data.get("vehicleId") or data.get("carId")
        if vehicle_id:
            vehicle.mqtt_vehicle_id = str(vehicle_id)
            return str(vehicle_id)
        return None

    def get_push_vehicle_identity(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any]:
        self._service_hub_get_metadata(token, vehicle)
        if not vehicle.mqtt_vehicle_id:
            self._service_hub_get_vehicle_id(token, vehicle)
            self._service_hub_get_metadata(token, vehicle)
        self._service_hub_validate_vehicle_identity(vehicle)
        return {
            "mqtt_vehicle_id": vehicle.mqtt_vehicle_id,
            "hu_client_id": vehicle.hu_client_id,
        }

    def get_push_credentials(self, token: Token) -> tuple[str, str] | None:
        return (
            self._service_hub_registered_client_id(token),
            (token.access_token or "").removeprefix("Bearer ").strip(),
        )
