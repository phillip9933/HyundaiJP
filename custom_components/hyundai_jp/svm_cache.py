"""Local SVM capture, render and preference state, independent of vehicle I/O."""

import asyncio
from collections.abc import Awaitable, Callable
import datetime as dt
from functools import partial
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ._api import SVMDetails
from ._api.svm_image import render_views

RenderExecutor = Callable[[Callable[[], dict[str, bytes]]], Awaitable[dict[str, bytes]]]


class SVMImageCache:
    """Share one render per capture/preference and reject superseded results.

    The coordinator supplies its tracked executor so rendering participates in
    the same cancellation and shutdown ownership as vehicle work. This helper
    never authenticates, fetches images or starts background tasks.
    """

    def __init__(self) -> None:
        self._details: dict[str, SVMDetails] = {}
        self._received_at: dict[str, dt.datetime] = {}
        self._dewarp: dict[str, bool] = {}
        self._views: dict[
            str, tuple[tuple[dt.datetime | None, bool], dict[str, bytes]]
        ] = {}
        self._render_locks: dict[str, asyncio.Lock] = {}
        self._render_diagnostics: dict[str, dict[str, Any]] = {}

    def get_details(self, vehicle_id: str) -> SVMDetails | None:
        """Return an already received capture without fetching new data."""
        return self._details.get(vehicle_id)

    def store(self, vehicle_id: str, details: SVMDetails) -> None:
        """A new capture invalidates rendered views, even with the same GPS time."""
        self._details[vehicle_id] = details
        self._received_at[vehicle_id] = dt_util.utcnow()
        self._views.pop(vehicle_id, None)

    def updated_at(self, vehicle_id: str) -> dt.datetime | None:
        """Use vehicle capture time, falling back to local receipt time."""
        details = self.get_details(vehicle_id)
        if details is None:
            return None
        return details.captured_at or self._received_at.get(vehicle_id)

    def dewarp_enabled(self, vehicle_id: str) -> bool:
        """Fisheye correction is off until the user enables it for this car."""
        return self._dewarp.get(vehicle_id, False)

    def set_dewarp(self, vehicle_id: str, enabled: bool) -> None:
        """Changing the preference makes the current render key obsolete."""
        self._dewarp[vehicle_id] = enabled

    async def async_render_views(
        self, vehicle_id: str, run_executor: RenderExecutor
    ) -> dict[str, bytes] | None:
        """Return all five views using a shared, cancellation-safe render job."""
        lock = self._render_locks.setdefault(vehicle_id, asyncio.Lock())
        async with lock:
            while True:
                details = self.get_details(vehicle_id)
                if (
                    details is None
                    or not details.image_bytes
                    or not details.image_sizes
                ):
                    self._render_diagnostics[vehicle_id] = {
                        "state": "awaiting_capture"
                        if details is None
                        else "missing_image_or_layout",
                        "view_count": 0,
                    }
                    return None
                key = (self.updated_at(vehicle_id), self.dewarp_enabled(vehicle_id))
                cached = self._views.get(vehicle_id)
                if cached is not None and cached[0] == key:
                    return cached[1]
                self._render_diagnostics[vehicle_id] = {
                    "state": "rendering",
                    "view_count": 0,
                }
                try:
                    views = await run_executor(
                        partial(render_views, details, dewarp=key[1])
                    )
                except Exception:
                    self._render_diagnostics[vehicle_id] = {
                        "state": "failed",
                        "view_count": 0,
                        "failure_category": "render_failed",
                    }
                    raise HomeAssistantError(
                        "Camera image could not be rendered"
                    ) from None
                if (
                    self.get_details(vehicle_id) is not details
                    or self.dewarp_enabled(vehicle_id) != key[1]
                ):
                    continue
                if not views:
                    self._render_diagnostics[vehicle_id] = {
                        "state": "failed",
                        "view_count": 0,
                        "failure_category": "invalid_image_or_layout",
                    }
                    raise HomeAssistantError(
                        "Camera image or layout could not be decoded"
                    )
                self._views[vehicle_id] = (key, views)
                self._render_diagnostics[vehicle_id] = {
                    "state": "complete",
                    "view_count": len(views),
                }
                return views

    def render_diagnostics(self) -> list[dict[str, Any]]:
        """Expose bounded outcomes without images or vehicle identifiers."""
        return [
            dict(summary) for summary in list(self._render_diagnostics.values())[:10]
        ]
