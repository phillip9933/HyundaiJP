"""Japan adapter shutdown attempts every existing session without creating more."""

from unittest.mock import Mock

import pytest
import requests

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi


@pytest.mark.parametrize("failed_stage", ["first", "second", "http"])
def test_close_attempts_all_helpers_and_http_after_an_earlier_failure(failed_stage):
    api = HyundaiBluelinkApi()
    events = []
    error = RuntimeError("PRIVATE_CLEANUP_DETAILS")

    def close(stage):
        events.append(stage)
        if stage == failed_stage:
            raise error

    first = Mock(close=Mock(side_effect=lambda: close("first")))
    second = Mock(close=Mock(side_effect=lambda: close("second")))
    api.session = Mock(spec=requests.Session)
    api.session.close.side_effect = lambda: close("http")
    api._mqtt_sessions = {"first": ((), first), "second": ((), second)}
    api._passive_state_observed_at["first"] = Mock()
    with pytest.raises(RuntimeError) as caught:
        api.close()
    assert caught.value is error
    assert events == ["first", "second", "http"]
    first.close.assert_called_once()
    second.close.assert_called_once()
    api.session.close.assert_called_once()
    assert api._mqtt_closed
    assert not api._mqtt_sessions
    assert not api._passive_state_observed_at


def test_close_only_uses_existing_resources_and_repeated_close_releases_no_helpers(
    monkeypatch,
):
    api = HyundaiBluelinkApi()
    api.session = Mock(spec=requests.Session)
    helper = Mock(close=Mock())
    api._mqtt_sessions = {"synthetic": ((), helper)}
    factory = Mock(side_effect=AssertionError("cleanup cannot create a session"))
    monkeypatch.setattr(
        "custom_components.hyundai_jp._api.client.JapanMqttSession", factory
    )
    api.close()
    api.close()
    factory.assert_not_called()
    helper.close.assert_called_once()
    # An existing HTTP pool may need reclosing after a late request finishes.
    assert api.session.close.call_count == 2
