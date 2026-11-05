import datetime as dt
from importlib import import_module

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.VehicleManager import VehicleManager


class DummyApi:
    def __init__(self):
        self.login_calls = 0
        self.refresh_calls = 0

    def login(self, username, password, pin=None):
        self.login_calls += 1
        return Token(
            username=username,
            password=password,
            valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        )

    def get_vehicles(self, token):
        return []

    def refresh_access_token(self, token):
        self.refresh_calls += 1
        return Token(
            username=token.username,
            valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        )


def test_check_and_refresh_token_handles_min_datetime(monkeypatch):
    dummy_api = DummyApi()
    monkeypatch.setattr(
        import_module("custom_components.hyundai_jp._api.VehicleManager"),
        "HyundaiBluelinkApi",
        lambda: dummy_api,
    )
    manager = VehicleManager(
        username="user",
        password="pass",
        pin="1234",
        geocode_api_enable=False,
    )
    manager.token = Token(
        username="test-user", password="test-pass", valid_until=dt.datetime.min
    )
    assert manager.check_and_refresh_token() is True
    assert dummy_api.refresh_calls == 1
    assert dummy_api.login_calls == 0


def test_login_returns_false_without_token(monkeypatch):
    dummy_api = DummyApi()
    monkeypatch.setattr(dummy_api, "login", lambda **kwargs: None)
    monkeypatch.setattr(
        import_module("custom_components.hyundai_jp._api.VehicleManager"),
        "HyundaiBluelinkApi",
        lambda: dummy_api,
    )
    manager = VehicleManager(username="user", password="pass", pin="1234")
    assert manager.login() is False


def test_commands_before_login_are_rejected_without_request(monkeypatch):
    from unittest.mock import Mock

    import pytest

    from custom_components.hyundai_jp._api.exceptions import AuthenticationError

    dummy_api = Mock(spec=HyundaiBluelinkApi)
    monkeypatch.setattr(
        import_module("custom_components.hyundai_jp._api.VehicleManager"),
        "HyundaiBluelinkApi",
        lambda: dummy_api,
    )
    manager = VehicleManager("synthetic", "synthetic", "0000")
    assert manager.token is None
    with pytest.raises(AuthenticationError, match="Sign in"):
        manager.lock("synthetic-car")
    dummy_api.lock_action.assert_not_called()
