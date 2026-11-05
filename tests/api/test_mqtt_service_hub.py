"""Japan Service Hub registration and subscription wire constants."""

from custom_components.hyundai_jp._api.mqtt_service_hub import (
    MQTT_PROTOCOL_ID_CCU_UPDATE,
    MQTT_PROTOCOL_ID_CONNECTION,
    MQTT_PROTOCOL_ID_RES,
    MQTT_PROTOCOL_ID_VSS,
    build_car_status_topics,
)


def test_topic_order_matches_the_japan_session_dispatcher():
    assert build_car_status_topics("vehicle", result_client_id="client") == [
        "service/phone/_/vss/vehicle",
        "service/phone/_/res/client",
        "service/phone/_/connection/vehicle",
    ]


def test_registration_protocols_match_the_japan_app():
    assert MQTT_PROTOCOL_ID_VSS == "service.phone.vss"
    assert MQTT_PROTOCOL_ID_CONNECTION == "service.phone.connection"
    assert MQTT_PROTOCOL_ID_RES == "service.phone.res"
    assert MQTT_PROTOCOL_ID_CCU_UPDATE == "statesync.vehicle.ccu.update"
