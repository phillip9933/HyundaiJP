"""MqttPushSession — push lifecycle over the ApiImpl contract (region-neutral).

Holds the vehicle-scoped Japan providers, callbacks and reconnect loop.
The providers own their shared command/push MQTT transport.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
import logging
import threading

from . import const
from .ApiImpl import ApiImpl, PushProvider
from .const import DOMAIN
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class MqttPushSession:
    """Provider lifecycle for one VehicleManager's push fleet.

    Holds vehicle-scoped providers, registered callbacks and the reconnect
    loop. The Japan API supplies a provider for each vehicle and owns the
    shared MQTT transport used by commands and push updates.
    """

    def __init__(
        self,
        api: ApiImpl,
        token_fn: Callable[[], Token],
        refresh_fn: Callable[[], object],
        get_vehicle: Callable[[str], Vehicle],
        after_close_fn: Callable[[], None] | None = None,
    ) -> None:
        self._api = api
        self._get_token = token_fn
        self._refresh_fn = refresh_fn
        self._get_vehicle = get_vehicle
        self._after_close_fn = after_close_fn
        self._vehicle_ids: set[str] = set()
        self._status_callback: Callable[[str, str], None] | None = None
        self._connection_change_callback: Callable[[bool], None] | None = None
        self._reconnect_cancel: threading.Event | None = None
        self._reconnect_lock = threading.Lock()
        self._provider_lock = threading.RLock()
        self._providers: dict[str, PushProvider] = {}
        self._provider_generation = 0
        self._closed = False

    @property
    def is_connected(self) -> bool:
        """Whether the push transport is connected."""
        with self._provider_lock:
            providers = list(self._providers.values())
        return any(provider.is_connected for provider in providers)

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        self._status_callback = callback

    def set_connection_change_callback(
        self, callback: Callable[[bool], None] | None
    ) -> None:
        self._connection_change_callback = callback

    def _generation_active(self, generation: int) -> bool:
        with self._provider_lock:
            return not self._closed and self._provider_generation == generation

    def ensure_started(self, vehicle_id: str) -> bool:
        """Register and connect unless stop or close invalidates this attempt."""
        with self._provider_lock:
            generation = self._provider_generation
            if self._closed:
                return False
        try:
            return self._ensure_started(vehicle_id, generation)
        finally:
            self._cleanup_closed_work()

    def _cleanup_closed_work(self) -> None:
        if self._closed and self._after_close_fn is not None:
            try:
                self._after_close_fn()
            except Exception as err:
                _LOGGER.warning(
                    "%s - Push late shutdown failed (%s)",
                    DOMAIN,
                    type(err).__name__[:64],
                )

    def _attempt_active(self, generation: int, cancel: threading.Event | None) -> bool:
        return self._generation_active(generation) and (
            cancel is None or not cancel.is_set()
        )

    def _ensure_started(
        self, vehicle_id: str, generation: int, cancel: threading.Event | None = None
    ) -> bool:
        token = self._get_token()
        vehicle = self._get_vehicle(vehicle_id)
        provider = self._api.get_push_provider(token, vehicle)
        if not self._attempt_active(generation, cancel):
            return False
        return self._start_provider(vehicle_id, provider, generation, cancel)

    def _start_provider(
        self,
        vehicle_id: str,
        provider: PushProvider,
        generation: int,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Attach a provider-owned session instead of creating another client."""
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                return False
            previous = self._providers.get(vehicle_id)
            self._providers[vehicle_id] = provider
            self._vehicle_ids.add(vehicle_id)
        if previous is not None and previous is not provider:
            previous.stop_push()

        def status(changed_vehicle: str, kind: str) -> None:
            with self._provider_lock:
                current = (
                    self._attempt_active(generation, cancel)
                    and self._providers.get(vehicle_id) is provider
                )
            if current and changed_vehicle == vehicle_id and self._status_callback:
                self._status_callback(vehicle_id, kind)

        def connection(connected: bool) -> None:
            with self._provider_lock:
                current = (
                    self._attempt_active(generation, cancel)
                    and self._providers.get(vehicle_id) is provider
                )
            if not current:
                return
            if self._connection_change_callback:
                self._connection_change_callback(connected)
            if not connected:
                self._on_push_disconnect("provider disconnected", generation)

        try:
            started = bool(provider.start_push(status, connection))
        except Exception:
            with self._provider_lock:
                stopped = not self._attempt_active(generation, cancel)
                if self._providers.get(vehicle_id) is provider:
                    self._providers.pop(vehicle_id, None)
                    self._vehicle_ids.discard(vehicle_id)
            provider.stop_push()
            if stopped:
                return False
            raise
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                if self._providers.get(vehicle_id) is provider:
                    self._providers.pop(vehicle_id, None)
                    self._vehicle_ids.discard(vehicle_id)
                stopped = True
            else:
                stopped = False
        if stopped:
            provider.stop_push()
            return False
        return started

    def close(self) -> None:
        """Permanently stop delivery and reject future startup attempts."""
        with self._provider_lock:
            if self._closed:
                return
            self._closed = True
        self.stop()

    def stop(self) -> None:
        """Disconnect push while allowing a later explicit restart."""
        with self._provider_lock:
            self._provider_generation += 1
            providers = list(self._providers.values())
            self._providers.clear()
            self._vehicle_ids.clear()
            cancel, self._reconnect_cancel = self._reconnect_cancel, None
        if cancel is not None:
            cancel.set()
        with ExitStack() as cleanup:
            for provider in providers:
                cleanup.callback(provider.stop_push)

    def _on_push_disconnect(
        self, reason: str, expected_generation: int | None = None
    ) -> None:
        """Reconnect after unexpected drops unless stop or close invalidates it."""
        _LOGGER.debug(f"{DOMAIN} - Push disconnected: {reason}")
        with self._provider_lock:
            if (
                self._closed
                or reason == "clean disconnect"
                or not self._vehicle_ids
                or (
                    expected_generation is not None
                    and expected_generation != self._provider_generation
                )
            ):
                return
            generation = self._provider_generation
            previous_cancel = self._reconnect_cancel
            cancel_event = threading.Event()
            self._reconnect_cancel = cancel_event
            vehicle_ids = list(self._vehicle_ids)
        if previous_cancel is not None:
            previous_cancel.set()

        def _reconnect_with_backoff() -> None:
            with ExitStack() as cleanup:
                cleanup.callback(self._cleanup_closed_work)
                delay = const.MQTT_PUSH_RECONNECT_INITIAL_DELAY
                while not cancel_event.is_set():
                    _LOGGER.debug(f"{DOMAIN} - Push reconnect attempt in {delay}s...")
                    if cancel_event.wait(delay):
                        return
                    with self._reconnect_lock:
                        if not self._attempt_active(generation, cancel_event):
                            return
                        try:
                            try:
                                self._refresh_fn()
                            except Exception as token_ex:
                                _LOGGER.warning(
                                    "%s - Push reconnect token refresh failed (%s)",
                                    DOMAIN,
                                    type(token_ex).__name__[:64],
                                )
                            if cancel_event.is_set() or not self._generation_active(
                                generation
                            ):
                                return
                            ok = all(
                                not cancel_event.is_set()
                                and self._generation_active(generation)
                                and self._ensure_started(vid, generation, cancel_event)
                                for vid in vehicle_ids
                            )
                            if ok and self.is_connected:
                                _LOGGER.debug(f"{DOMAIN} - Push reconnected")
                                return
                        except Exception as ex:
                            _LOGGER.warning(
                                "%s - Push reconnect failed (%s)",
                                DOMAIN,
                                type(ex).__name__[:64],
                            )
                    delay = min(delay * 2, const.MQTT_PUSH_RECONNECT_MAX_DELAY)
                _LOGGER.debug(f"{DOMAIN} - Push reconnect cancelled")

        threading.Thread(
            target=_reconnect_with_backoff, name="push-reconnect", daemon=True
        ).start()
