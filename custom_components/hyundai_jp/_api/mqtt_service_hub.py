"""Japan Service Hub protocol identifiers and subscription topics."""

from __future__ import annotations

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
