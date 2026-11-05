"""The Hyundai Bluelink JP integration."""

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import HyundaiJPConfigEntry, JapanDataUpdateCoordinator
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.CLIMATE,
    Platform.IMAGE,
    Platform.TIME,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register account-independent services once for this integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, config_entry: HyundaiJPConfigEntry
) -> bool:
    """Set up Hyundai Bluelink JP from a config entry."""
    coordinator = JapanDataUpdateCoordinator(hass, config_entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        await _async_shutdown_coordinator(coordinator)
        raise
    except Exception as ex:
        await _async_shutdown_coordinator(coordinator)
        raise ConfigEntryNotReady(f"Config Not Ready: {ex}") from ex

    config_entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def _async_shutdown_coordinator(
    coordinator: JapanDataUpdateCoordinator,
) -> None:
    try:
        await coordinator.async_shutdown()
    except Exception as err:
        _LOGGER.warning(
            "Unable to shut down vehicle connection (%s)", type(err).__name__[:64]
        )


async def async_unload_entry(
    hass: HomeAssistant, config_entry: HyundaiJPConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    ):
        await _async_shutdown_coordinator(config_entry.runtime_data)
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: HyundaiJPConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    return True
