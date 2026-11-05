"""Device Tracker for HyundaiJP integration."""

from __future__ import annotations

import logging

# Canonical import path since HA 2026.6 (config_entry alias removed 2027.6);
# the ignore covers HA's implicit re-export, rejected by mypy --strict.
from homeassistant.components.device_tracker import TrackerEntity  # type: ignore[attr-defined]
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._api import Vehicle
from .const import DOMAIN
from .entity import HyundaiKiaConnectEntity
from .japan_coordinator import JapanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the device tracker from a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.unique_id]
    entities = []
    for vehicle_id in coordinator.vehicle_manager.vehicles:
        vehicle: Vehicle = coordinator.vehicle_manager.vehicles[vehicle_id]
        if vehicle.location is not None:
            entities.append(HyundaiKiaConnectTracker(coordinator, vehicle))

    async_add_entities(entities)


PARALLEL_UPDATES = 0


class HyundaiKiaConnectTracker(TrackerEntity, HyundaiKiaConnectEntity):
    """Tracks the vehicle's GPS location."""

    def __init__(
        self,
        coordinator: JapanDataUpdateCoordinator,
        vehicle: Vehicle,
    ) -> None:
        """Initialize the tracker."""
        HyundaiKiaConnectEntity.__init__(self, coordinator, vehicle)
        self._attr_unique_id = f"{DOMAIN}_{vehicle.id}_location"
        self._attr_translation_key = "location"
        self._attr_icon = "mdi:map-marker-outline"

    @property
    def latitude(self) -> float | None:
        """Return the vehicle's latitude."""
        latitude: float | None = self.vehicle.location_latitude
        return latitude

    @property
    def longitude(self) -> float | None:
        """Return the vehicle's longitude."""
        longitude: float | None = self.vehicle.location_longitude
        return longitude

    @property
    def source_type(self) -> SourceType:
        """Return the source type (GPS)."""
        return SourceType.GPS
