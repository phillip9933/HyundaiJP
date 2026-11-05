"""Image entities for the Surround View Monitor (SVM / Find My Car).

The SVM image is a single composite JPEG (4472x720) containing 5 segments
side-by-side, in order: FRONT, REAR, LEFT, RIGHT, TOP (bird's-eye). The
library (._api.svm_image) crops, dewarps and encodes the
views; this integration only serves the rendered bytes — rendered once per
capture on the coordinator (coordinator.async_get_svm_views), not per
entity request.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from ._api import Vehicle
from .const import DOMAIN
from .coordinator import SIGNAL_SVM_RENDER, JapanDataUpdateCoordinator
from .entity import HyundaiKiaConnectEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


@dataclass(frozen=True)
class SVMView:
    """One SVM composite segment."""

    key: str
    translation_key: str
    icon: str


# Presentation order follows the composite layout. Cropping and segment widths
# are owned by _api.svm_image; these records only describe the HA image entities.
SVM_VIEWS: tuple[SVMView, ...] = (
    SVMView("front", "svm_front", "mdi:car-arrow-right"),
    SVMView("rear", "svm_rear", "mdi:car-arrow-left"),
    SVMView("left", "svm_left", "mdi:car-side"),
    SVMView("right", "svm_right", "mdi:car-side"),
    SVMView("top", "svm_top", "mdi:car-360"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up image platform."""
    coordinator: JapanDataUpdateCoordinator = hass.data[DOMAIN][config_entry.unique_id]

    entities = []
    for vehicle_id in coordinator.vehicle_manager.vehicles:
        vehicle: Vehicle = coordinator.vehicle_manager.vehicles[vehicle_id]
        if vehicle.supports.svm:
            # Best-effort: populate the cache so the image entities are
            # available immediately after restart instead of waiting for a
            # manual capture. get_svm_details is a cheap cached GET — it does
            # not wake the car.
            try:
                await coordinator.async_get_svm_details(vehicle_id)
            except Exception:
                _LOGGER.debug(
                    "SVM initial fetch failed for %s", vehicle_id, exc_info=True
                )
            for view in SVM_VIEWS:
                entities.append(SVMImageEntity(coordinator, vehicle, view))

    async_add_entities(entities)


class SVMImageEntity(ImageEntity, HyundaiKiaConnectEntity):
    """One SVM camera view (served from the coordinator's render cache)."""

    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        coordinator: JapanDataUpdateCoordinator,
        vehicle: Vehicle,
        view: SVMView,
    ) -> None:
        """Initialize the SVM image entity."""
        # ImageEntity.__init__ sets up the image-proxy client + access_tokens
        # (it does not call super().__init__, so call it explicitly).
        ImageEntity.__init__(self, coordinator.hass)
        HyundaiKiaConnectEntity.__init__(self, coordinator, vehicle)
        self._view = view
        self._attr_translation_key = view.translation_key
        self._attr_icon = view.icon
        self._attr_unique_id = f"{DOMAIN}_{vehicle.id}_svm_{view.key}"
        self._attr_image_last_updated = coordinator.get_svm_image_updated_at(vehicle.id)

    def _handle_coordinator_update(self) -> None:
        """Refresh the capture timestamp when the coordinator pushes an update."""
        self._attr_image_last_updated = self.coordinator.get_svm_image_updated_at(
            self.vehicle.id
        )
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Subscribe to the coordinator's SVM render-change signal."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SVM_RENDER.format(self.vehicle.id),
                self._async_handle_render_change,
            )
        )

    @callback
    def _async_handle_render_change(self) -> None:
        """Re-serve the image after a dewarp toggle changed the render key.

        A new timestamp forces the image proxy to fetch again; the render
        itself is cached on the coordinator under the new key and reused.
        """
        self._attr_image_last_updated = dt_util.now()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if a cached image is available."""
        return self.coordinator.get_cached_svm_details(self.vehicle.id) is not None

    async def async_image(self) -> bytes | None:
        """Return this view's JPEG bytes from the coordinator's render cache."""
        views = await self.coordinator.async_get_svm_views(self.vehicle.id)
        if views is None:
            return None
        return views.get(self._view.key)
