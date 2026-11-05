"""Terminal shutdown cannot be undone by provider registration or reconnect."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, current_thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from custom_components.hyundai_jp._api import const
from custom_components.hyundai_jp._api.mqtt_push_session import MqttPushSession
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.Vehicle import Vehicle
from tests.api.test_mqtt_provider_lifecycle import Provider, session


@pytest.fixture
def scoped():
    vehicle = Vehicle(id="synthetic")
    provider = Provider()
    original_start, original_stop = provider.start_push, provider.stop_push
    provider.start_push = Mock(side_effect=original_start)
    provider.stop_push = Mock(side_effect=original_stop)
    api = SimpleNamespace(get_push_provider=Mock(return_value=provider), close=Mock())
    owner = MqttPushSession(
        api=api,
        token_fn=lambda: Token(username="synthetic", password="synthetic"),
        refresh_fn=Mock(),
        get_vehicle=lambda _: vehicle,
        after_close_fn=api.close,
    )
    return owner, api, provider


@pytest.mark.parametrize("stage", ["lookup", "start"])
def test_close_during_registration_cannot_start_later_stages(scoped, stage):
    owner, api, provider = scoped
    entered, release = Event(), Event()
    operation = api.get_push_provider if stage == "lookup" else provider.start_push
    original = operation.side_effect

    def block(*args):
        entered.set()
        assert release.wait(2)
        return original(*args) if callable(original) else operation.return_value

    operation.side_effect = block
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.ensure_started, "synthetic")
        assert entered.wait(2)
        owner.close()
        release.set()
        assert pending.result(timeout=2) is False
    if stage == "lookup":
        provider.start_push.assert_not_called()
    assert not provider.is_connected
    assert not owner.is_connected


def test_close_during_reconnect_token_refresh_never_registers_again(
    scoped, monkeypatch
):
    owner, api, provider = scoped
    assert owner.ensure_started("synthetic")
    monkeypatch.setattr(const, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0)
    entered, release = Event(), Event()
    workers = []

    def refresh():
        workers.append(current_thread())
        entered.set()
        assert release.wait(2)

    owner._refresh_fn = refresh
    owner._on_push_disconnect("synthetic disconnect")
    assert entered.wait(2)
    calls = api.get_push_provider.call_count
    from tests.api.test_manager_cleanup import manager

    fleet = manager()
    fleet.api = api
    fleet._push_session = owner
    fleet.close()
    release.set()
    workers[0].join(2)
    assert not workers[0].is_alive()
    assert api.close.call_count == 2
    assert api.get_push_provider.call_count == calls
    assert not provider.is_connected
    assert not owner.is_connected


def test_close_during_provider_connect_disconnects_late_connection(scoped):
    owner, _, provider = scoped
    entered, release = Event(), Event()
    original = provider.start_push.side_effect

    def connect(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    provider.start_push.side_effect = connect
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.ensure_started, "synthetic")
        assert entered.wait(2)
        owner.close()
        release.set()
        assert pending.result(timeout=2) is False
    assert not owner.is_connected
    assert not provider.is_connected
    assert provider.stop_push.call_count == 2


def test_stop_allows_restart_but_close_is_terminal(scoped):
    owner, api, provider = scoped
    assert owner.ensure_started("synthetic")
    owner.stop()
    assert owner.ensure_started("synthetic")
    assert provider.start_push.call_count == 2
    owner.close()
    calls = api.get_push_provider.call_count
    assert owner.ensure_started("synthetic") is False
    assert api.get_push_provider.call_count == calls
    assert not owner.is_connected


def test_terminal_close_preserves_owned_provider_stop_during_start():
    provider = Provider()
    owner = session(provider)
    entered, release = Event(), Event()
    original = provider.start_push

    def block(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    provider.start_push = block
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.ensure_started, "synthetic-car")
        assert entered.wait(2)
        owner.close()
        release.set()
        assert pending.result(timeout=2) is False
    assert owner.ensure_started("synthetic-car") is False
    assert not provider.is_connected


def test_close_attempts_all_providers_even_if_one_fails(scoped):
    owner, _, provider = scoped
    assert owner.ensure_started("synthetic")
    first = SimpleNamespace(stop_push=Mock())
    second = SimpleNamespace(stop_push=Mock(side_effect=RuntimeError("stop")))
    owner._providers = {"first": first, "second": second, "synthetic": provider}
    with pytest.raises(RuntimeError, match="stop"):
        owner.close()
    first.stop_push.assert_called_once()
    second.stop_push.assert_called_once()
    assert not provider.is_connected
    assert owner.ensure_started("synthetic") is False


def test_reconnect_finishing_after_close_releases_reopened_http_pool(
    scoped, monkeypatch
):
    from tests.api.test_manager_cleanup import manager

    owner, api, _ = scoped
    assert owner.ensure_started("synthetic")
    monkeypatch.setattr(const, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0)
    entered, release = Event(), Event()
    workers = []
    reopened = Mock(spec=requests.Session)
    api.session = Mock(spec=requests.Session)
    api.close.side_effect = lambda: api.session.close()

    def refresh():
        workers.append(current_thread())
        entered.set()
        assert release.wait(2)
        api.session = reopened

    owner._refresh_fn = refresh
    owner._on_push_disconnect("synthetic disconnect")
    assert entered.wait(2)
    fleet = manager()
    fleet.api, fleet._push_session = api, owner
    fleet.close()
    reopened.close.assert_not_called()
    release.set()
    workers[0].join(2)
    assert not workers[0].is_alive()
    reopened.close.assert_called_once()
    assert not owner.is_connected


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("refresh", "Push reconnect token refresh failed"),
        ("registration", "Push reconnect failed"),
    ],
)
def test_reconnect_errors_log_stage_and_type_without_private_message(
    scoped, monkeypatch, caplog, stage, expected
):
    owner, api, _ = scoped
    assert owner.ensure_started("synthetic")
    monkeypatch.setattr(const, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0)
    caplog.set_level("WARNING")
    entered = Event()
    workers = []

    def fail(*_args):
        workers.append(current_thread())
        entered.set()
        owner.close()
        raise RuntimeError("PRIVATE_ACCOUNT_TOKEN_AND_RESPONSE")

    if stage == "refresh":
        owner._refresh_fn = fail
    else:
        api.get_push_provider.side_effect = fail
    owner._on_push_disconnect("synthetic disconnect")
    assert entered.wait(2)
    workers[0].join(2)
    assert not workers[0].is_alive()
    assert expected + " (RuntimeError)" in caplog.text
    assert "PRIVATE_ACCOUNT_TOKEN_AND_RESPONSE" not in caplog.text


def test_reconnect_recovers_failed_provider_when_other_vehicle_is_connected(
    monkeypatch,
):
    providers = {"first": Provider(), "second": Provider()}
    vehicles = {key: Vehicle(id=key) for key in providers}
    restarted = Event()
    workers, calls = [], []
    original = providers["first"].start_push

    def first_start(*args):
        calls.append(1)
        if len(calls) > 1:
            workers.append(current_thread())
            restarted.set()
        return original(*args)

    providers["first"].start_push = first_start
    owner = MqttPushSession(
        api=SimpleNamespace(get_push_provider=lambda token, car: providers[car.id]),
        token_fn=lambda: Token(username="synthetic", password="synthetic"),
        refresh_fn=Mock(),
        get_vehicle=vehicles.__getitem__,
    )
    monkeypatch.setattr(const, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0)
    assert owner.ensure_started("first")
    assert owner.ensure_started("second")
    providers["first"].is_connected = False
    providers["first"].connection(False)
    assert restarted.wait(2)
    workers[0].join(2)
    assert not workers[0].is_alive()
    assert providers["first"].is_connected
    assert providers["second"].is_connected
    assert providers["second"].stop_count == 0
    owner.close()


def test_superseded_reconnect_cannot_rebuild_alongside_new_worker(scoped, monkeypatch):
    owner, api, provider = scoped
    assert owner.ensure_started("synthetic")
    entered, release, waiting, next_refresh = Event(), Event(), Event(), Event()
    workers, attempts = [], []
    lock = Lock()

    class ObservedRebuildLock:
        def __enter__(self):
            attempts.append(1)
            if len(attempts) == 2:
                waiting.set()
            lock.acquire()

        def __exit__(self, *_args):
            lock.release()

    def refresh():
        workers.append(current_thread())
        if len(workers) == 1:
            entered.set()
            assert release.wait(2)
        else:
            next_refresh.set()

    owner._reconnect_lock = ObservedRebuildLock()
    owner._refresh_fn = refresh
    monkeypatch.setattr(const, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0)
    owner._on_push_disconnect("first synthetic disconnect")
    assert entered.wait(2)
    owner._on_push_disconnect("second synthetic disconnect")
    assert waiting.wait(2)
    assert len(workers) == 1
    release.set()
    assert next_refresh.wait(2)
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    assert api.get_push_provider.call_count == 2
    assert provider.start_push.call_count == 2
    assert owner.is_connected
    owner.close()
