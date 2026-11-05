"""Japan Service Hub wire contracts and registration orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from .Token import Token
from .Vehicle import Vehicle

TOPIC_PREFIX_RESULT = "service/phone/_/res/"
TOPIC_PREFIX_VSS = "service/phone/_/vss/"  # CarStatus.Status
TOPIC_PREFIX_CONNECTION = "service/phone/_/connection/"  # CarStatus.Connect

# Protocol IDs — sent as plain strings in the Service Hub body
MQTT_PROTOCOL_ID_VSS = "service.phone.vss"
MQTT_PROTOCOL_ID_CONNECTION = "service.phone.connection"
MQTT_PROTOCOL_ID_RES = "service.phone.res"
MQTT_PROTOCOL_ID_CCU_UPDATE = "statesync.vehicle.ccu.update"


def build_car_status_topics(vehicle_id: str, *, result_client_id: str) -> list[str]:
    """Build Japan's status, command-result and connection subscriptions."""
    return [
        TOPIC_PREFIX_VSS + vehicle_id,
        TOPIC_PREFIX_RESULT + result_client_id,
        TOPIC_PREFIX_CONNECTION + vehicle_id,
    ]


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
        }

    def get_push_credentials(self, token: Token) -> tuple[str, str] | None:
        return (
            self._service_hub_registered_client_id(token),
            (token.access_token or "").removeprefix("Bearer ").strip(),
        )
