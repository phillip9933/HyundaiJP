"""Token serialization."""

from dataclasses import asdict
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


def test_legacy_password_is_accepted_without_retaining_or_exporting_it():
    secret = "SYNTHETIC_LEGACY_PASSWORD"
    token = Token(username="user", password=secret)
    assert "password" not in vars(token)
    assert "password" not in asdict(token)
    assert "password" not in token.to_dict()
    assert secret not in repr(token)


def test_saved_tokens_load_with_or_without_legacy_password():
    legacy = {
        "username": "synthetic-user",
        "password": "SYNTHETIC_LEGACY_PASSWORD",
        "access_token": "synthetic-access",
        "refresh_token": "synthetic-refresh",
        "mqtt_registration_uuid": "synthetic-registration",
    }
    token = Token.from_dict(legacy)
    assert "password" not in token.to_dict()
    assert token.access_token == legacy["access_token"]
    assert token.refresh_token == legacy["refresh_token"]
    assert token.mqtt_registration_uuid == legacy["mqtt_registration_uuid"]
    assert Token.from_dict(token.to_dict()) == token


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
        "stamp",
    }
    assert not serialized.keys() & discarded
    # Historical dictionaries may carry other-region keys; no migration leaks them.
    assert (
        Token.from_dict(
            {**serialized, "cci_access_token": "ignored", "stamp": "ignored"}
        )
        == token
    )
