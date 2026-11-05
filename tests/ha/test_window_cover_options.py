"""Tests for the window cover's WindowRequestOptions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp._api.ApiImpl import WindowRequestOptions
from custom_components.hyundai_jp._api.const import WINDOW_STATE
from custom_components.hyundai_jp.cover import (
    COVER_DESCRIPTIONS,
    HyundaiKiaConnectCover,
)


@pytest.mark.parametrize("description", COVER_DESCRIPTIONS, ids=lambda d: d.key)
@pytest.mark.asyncio
async def test_open_sets_only_this_window(description) -> None:
    coordinator = MagicMock()
    coordinator.async_set_windows = AsyncMock()
    vehicle = Vehicle(id="v1")
    cover = HyundaiKiaConnectCover(coordinator, description, vehicle)

    await cover.async_open_cover()

    coordinator.async_set_windows.assert_awaited_once_with(
        "v1",
        WindowRequestOptions(**{description.window_position: WINDOW_STATE.OPEN}),
    )
