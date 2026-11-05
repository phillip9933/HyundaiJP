"""A nonexistent window-stop command must not close the window instead."""

from unittest.mock import AsyncMock, Mock

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.cover import COVER_DESCRIPTIONS, HyundaiBluelinkCover


async def test_window_stop_is_not_advertised_or_mapped_to_close():
    vehicle = Vehicle(id="synthetic", front_left_window_is_open=True)
    coordinator = Mock()
    coordinator.async_set_windows = AsyncMock()
    window = HyundaiBluelinkCover(coordinator, COVER_DESCRIPTIONS[0], vehicle)
    assert not window.supported_features & CoverEntityFeature.STOP
    with pytest.raises(HomeAssistantError, match="not supported"):
        await window.async_stop_cover()
    coordinator.async_set_windows.assert_not_called()
