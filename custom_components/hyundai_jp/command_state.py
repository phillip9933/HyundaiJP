"""Bounded command intent, separate from the vehicle's confirmed telemetry."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import datetime as dt
import time
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from ._api import Vehicle
from ._api.const import ORDER_STATUS


@dataclass
class _PendingCommand:
    target: bool
    baseline: dt.datetime | None
    started_at: dt.datetime
    deadline: float
    phase: str = "submitting"
    action_id: str | None = None
    timer: asyncio.TimerHandle | None = None


class PendingCommandTracker:
    """Keep UI progress until fresh matching state, rejection or timeout.

    This object never sends a request or changes a Vehicle. The coordinator
    owns the network work, passive state watcher and cleanup ordering.
    """

    TIMEOUT_SECONDS = 120
    # OAuth, broker setup and the bounded 140s control request may precede
    # service acceptance. Do not expire progress halfway through that work.
    SUBMISSION_TIMEOUT_SECONDS = 10 * 60

    def __init__(self, notify: Callable[[], None]) -> None:
        self._notify = notify
        self._pending: dict[tuple[str, str], _PendingCommand] = {}
        self._outcomes: dict[tuple[str, str], str] = {}
        self._closed = False

    @staticmethod
    def _timestamp(value: object) -> dt.datetime | None:
        if not isinstance(value, dt.datetime):
            return None
        return value.replace(tzinfo=value.tzinfo or dt.UTC).astimezone(dt.UTC)

    def target(self, vehicle_id: str, actuator: str) -> bool | None:
        """Read pending intent without presenting it as confirmed state."""
        pending = self._pending.get((vehicle_id, actuator))
        if pending is None or time.monotonic() >= pending.deadline:
            return None
        return pending.target

    def has_pending(self, vehicle_id: str) -> bool:
        return any(
            key[0] == vehicle_id and time.monotonic() < pending.deadline
            for key, pending in self._pending.items()
        )

    def begin(self, vehicle: Vehicle, actuator: str, target: bool) -> None:
        """Reject repeated/conflicting clicks on one still-pending actuator."""
        if self._closed:
            raise HomeAssistantError("The integration is shutting down")
        key = (vehicle.id, actuator)
        old = self._pending.get(key)
        if old is not None:
            if time.monotonic() < old.deadline:
                raise HomeAssistantError(
                    "This vehicle control is still awaiting confirmation; "
                    "wait for its state update before trying again"
                )
            self._finish(key, "timed_out", notify=False)
        pending = _PendingCommand(
            target=target,
            baseline=self._timestamp(vehicle.last_updated_at),
            started_at=dt.datetime.now(dt.UTC),
            deadline=time.monotonic() + self.SUBMISSION_TIMEOUT_SECONDS,
        )
        self._pending[key] = pending
        self._outcomes.pop(key, None)
        self._arm(key, pending)
        self._notify()

    def _arm(self, key: tuple[str, str], pending: _PendingCommand) -> None:
        if pending.timer is not None:
            pending.timer.cancel()
        pending.timer = asyncio.get_running_loop().call_later(
            max(0, pending.deadline - time.monotonic()), self._expire, key, pending
        )

    def _expire(self, key: tuple[str, str], pending: _PendingCommand) -> None:
        if self._pending.get(key) is pending:
            self._finish(key, "timed_out")

    def submitted(self, vehicle_id: str, actuator: str, action_id: str) -> None:
        pending = self._pending.get((vehicle_id, actuator))
        if pending is None or self._closed:
            return
        pending.action_id = action_id
        pending.phase = "awaiting_telemetry"
        pending.deadline = time.monotonic() + self.TIMEOUT_SECONDS
        self._arm((vehicle_id, actuator), pending)
        self._notify()

    def unknown(self, vehicle_id: str, actuator: str) -> None:
        """An ambiguous submission is observed, never automatically replayed."""
        pending = self._pending.get((vehicle_id, actuator))
        if pending is None or self._closed:
            return
        pending.phase = "submission_unknown"
        pending.deadline = time.monotonic() + self.TIMEOUT_SECONDS
        self._arm((vehicle_id, actuator), pending)
        self._notify()

    def receipt(
        self, vehicle_id: str, action_id: str, status: ORDER_STATUS | None
    ) -> None:
        for key, pending in list(self._pending.items()):
            if key[0] != vehicle_id or pending.action_id != action_id:
                continue
            if status == ORDER_STATUS.FAILED:
                self._finish(key, "rejected")
            elif status in (ORDER_STATUS.SUCCESS, ORDER_STATUS.ACCEPTED):
                pending.phase = "awaiting_telemetry"
            else:
                pending.phase = "completion_unknown"

    def failed(self, vehicle_id: str, actuator: str) -> None:
        self._finish((vehicle_id, actuator), "rejected")

    def phase(self, vehicle_id: str, actuator: str) -> str | None:
        pending = self._pending.get((vehicle_id, actuator))
        if pending is not None and time.monotonic() < pending.deadline:
            return pending.phase
        return self._outcomes.get((vehicle_id, actuator))

    def observe(self, vehicles: Mapping[str, Vehicle]) -> None:
        """Reconcile during publication; old snapshots never clear intent."""
        for key, pending in list(self._pending.items()):
            if time.monotonic() >= pending.deadline:
                self._finish(key, "timed_out", notify=False)
                continue
            vehicle = vehicles.get(key[0])
            if vehicle is None:
                self._finish(key, "vehicle_removed", notify=False)
                continue
            timestamp = self._timestamp(vehicle.last_updated_at)
            if timestamp is None:
                continue
            fresh = (
                timestamp > pending.baseline
                if pending.baseline is not None
                else timestamp >= pending.started_at
            )
            value = (
                vehicle.is_locked
                if key[1] == "door_lock"
                else vehicle.ev_charge_port_door_is_open
            )
            if fresh and value is not None and value == pending.target:
                self._finish(key, "confirmed", notify=False)

    def _finish(
        self, key: tuple[str, str], outcome: str, *, notify: bool = True
    ) -> None:
        pending = self._pending.pop(key, None)
        if pending is None:
            return
        if pending.timer is not None:
            pending.timer.cancel()
        if len(self._outcomes) >= 32 and key not in self._outcomes:
            self._outcomes.pop(next(iter(self._outcomes)))
        self._outcomes[key] = outcome
        if notify:
            self._notify()

    def diagnostics(self) -> list[dict[str, Any]]:
        """No vehicle identifiers, action IDs or exact timestamps are exported."""
        active = [
            {
                "actuator": actuator,
                "state": pending.phase,
                "seconds_remaining": max(0, round(pending.deadline - time.monotonic())),
            }
            for (_, actuator), pending in list(self._pending.items())[:32]
        ]
        completed = [
            {"actuator": actuator, "state": outcome}
            for (_, actuator), outcome in self._outcomes.items()
        ]
        return (active + completed)[:32]

    def close(self) -> None:
        """Stop every local timer; late command completions cannot rearm one."""
        self._closed = True
        for pending in self._pending.values():
            if pending.timer is not None:
                pending.timer.cancel()
        self._pending.clear()
        self._outcomes.clear()
