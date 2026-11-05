"""Tests for the push-contract defaults on the ApiImpl base."""

import pytest

from custom_components.hyundai_jp._api.ApiImpl import ApiImpl
from custom_components.hyundai_jp._api.const import MQTT_PUSH_CONNECTION_STATE
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.Vehicle import Vehicle


def test_capabilities_live_on_vehicle_supports():
    vehicle = Vehicle(id="v1", name="test", model="test")
    # Tri-state: unknown until the region's parser sets it.
    assert vehicle.supports.mqtt_push is None


@pytest.mark.parametrize(
    "call",
    [
        lambda impl: impl.get_push_broker_info(Token(username="u", password="p")),
        lambda impl: impl.register_push_client(Token(username="u", password="p")),
        lambda impl: impl.register_push_vehicle(
            Token(username="u", password="p"), Vehicle(id="v1")
        ),
        lambda impl: impl.get_push_vehicle_identity(
            Token(username="u", password="p"), Vehicle(id="v1")
        ),
        lambda impl: impl.get_push_connection_state(Token(username="u", password="p")),
        lambda impl: impl.get_push_topics(
            Token(username="u", password="p"), Vehicle(id="v1")
        ),
        lambda impl: impl.parse_push_message("topic", b"payload"),
    ],
)
def test_base_push_methods_not_implemented(call):
    """Base defaults raise NotImplementedError like the rest of ApiImpl."""
    with pytest.raises(NotImplementedError):
        call(ApiImpl())


def test_connection_state_enum_values():
    expected = {v.value for v in MQTT_PUSH_CONNECTION_STATE}
    assert expected == {"ONLINE", "OFFLINE", "UNKNOWN"}
