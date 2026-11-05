"""Retiring authentication never skips helpers or obscures setup failures."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from custom_components.hyundai_jp._api import Token, Vehicle
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.exceptions import APIError


def _rotation(monkeypatch, factory):
    api = HyundaiBluelinkApi()
    token = Token(
        username="synthetic",
        access_token="new",
        device_id="phone",
        mqtt_registration_uuid="registration",
    )
    first = Mock(closed=False, failed=False)
    second = Mock(closed=False, failed=False)
    api._mqtt_sessions = {
        "one": (("old", "phone", "registration"), first),
        "two": (("old", "phone", "registration"), second),
    }
    monkeypatch.setattr(
        "custom_components.hyundai_jp._api.client.JapanMqttSession", factory
    )
    return api, token, first, second


def test_every_retired_helper_is_closed_even_when_the_first_close_fails(monkeypatch):
    replacement = Mock(closed=False, failed=False)
    factory = Mock(return_value=replacement)
    api, token, first, second = _rotation(monkeypatch, factory)
    failure = RuntimeError("synthetic cleanup failure")
    first.close.side_effect = failure
    try:
        with pytest.raises(RuntimeError) as caught:
            api._mqtt_for_vehicle(token, Vehicle(id="one"))
        assert caught.value is failure
        first.close.assert_called_once()
        second.close.assert_called_once()
        assert not api._mqtt_retiring
        assert api._mqtt_for_vehicle(token, Vehicle(id="one")) is replacement
        factory.assert_called_once()
    finally:
        api.close()
    replacement.close.assert_called_once()


def test_setup_error_survives_multiple_cleanup_failures(monkeypatch, caplog):
    primary = ValueError("synthetic setup failure")
    factory = Mock(side_effect=primary)
    api, token, first, second = _rotation(monkeypatch, factory)
    first.close.side_effect = RuntimeError("PRIVATE_FIRST_CLEANUP")
    second.close.side_effect = RuntimeError("PRIVATE_SECOND_CLEANUP")
    try:
        with pytest.raises(ValueError) as caught:
            api._mqtt_for_vehicle(token, Vehicle(id="one"))
        assert caught.value is primary
        first.close.assert_called_once()
        second.close.assert_called_once()
        assert not api._mqtt_retiring
        assert api._mqtt_sessions == {}
        assert "RuntimeError" in caplog.text
        assert "PRIVATE_" not in caplog.text
        # A later explicit retry, after cleanup, may create its own helper.
        replacement = Mock(closed=False, failed=False)
        factory.side_effect = None
        factory.return_value = replacement
        assert api._mqtt_for_vehicle(token, Vehicle(id="one")) is replacement
    finally:
        api.close()


@pytest.mark.parametrize("setup_fails", [False, True])
def test_cleanup_callback_can_reenter_without_creating_new_resources(
    monkeypatch, setup_fails
):
    replacement = Mock(closed=False, failed=False)
    factory = Mock(return_value=replacement)
    if setup_fails:
        factory.side_effect = ValueError("synthetic setup failure")
    api, token, first, second = _rotation(monkeypatch, factory)
    reentered = []

    def callback():
        # A different vehicle or old authentication must not create or
        # evict anything while disconnected-session callbacks are running.
        for vehicle_id in ("two", "new"):
            with pytest.raises(APIError, match="cleanup is in progress"):
                api._mqtt_for_vehicle(token, Vehicle(id=vehicle_id))
        if not setup_fails:
            assert api._mqtt_for_vehicle(token, Vehicle(id="one")) is replacement
        reentered.append(True)

    try:
        # Enter from another thread to prove callbacks run outside the lock.
        with ThreadPoolExecutor(max_workers=1) as executor:
            first.close.side_effect = lambda: executor.submit(callback).result(2)
            if setup_fails:
                with pytest.raises(ValueError, match="setup failure"):
                    api._mqtt_for_vehicle(token, Vehicle(id="one"))
            else:
                assert api._mqtt_for_vehicle(token, Vehicle(id="one")) is replacement
        assert reentered == [True]
        second.close.assert_called_once()
        factory.assert_called_once()
        assert not api._mqtt_retiring
    finally:
        api.close()
