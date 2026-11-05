"""Token serialization."""

import datetime as dt

from custom_components.hyundai_jp._api.Token import Token


def test_from_dict_without_valid_until_is_expired() -> None:
    """A stored token without valid_until falls back to the field default
    (expired, forcing a re-login) instead of None."""
    token = Token.from_dict({"username": "user", "password": "pass"})
    assert token.valid_until == dt.datetime.min


def test_round_trip_keeps_valid_until() -> None:
    valid_until = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
    token = Token(username="user", password="pass", valid_until=valid_until)
    assert Token.from_dict(token.to_dict()).valid_until == valid_until


def test_japan_identity_survives_round_trip_without_discarded_auth_fields():
    token = Token(
        username="synthetic",
        password="synthetic",
        access_token="synthetic-access",
        refresh_token="synthetic-refresh",
        device_id="synthetic-device",
        mqtt_client_id="synthetic-client",
        mqtt_registration_uuid="synthetic-registration",
        control_token="Bearer synthetic-control",
        control_token_expiry=123.75,
    )
    serialized = token.to_dict()
    assert Token.from_dict(serialized) == token
    discarded = {
        "cci_access_token",
        "exchangeable_token",
        "exchangeable_refresh_token",
        "non_ccs_token",
        "non_ccs_refresh_token",
        "id_token",
        "user_id",
    }
    assert not serialized.keys() & discarded
    # Historical dictionaries may carry other-region keys; no migration leaks them.
    assert Token.from_dict({**serialized, "cci_access_token": "ignored"}) == token
