"""Cover for Hyundai Bluelink JP integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityDescription,
    CoverEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._api import Vehicle, WindowRequestOptions
from ._api.const import WINDOW_STATE
from .const import DOMAIN
from .coordinator import HyundaiJPConfigEntry, JapanDataUpdateCoordinator
from .entity import HyundaiBluelinkEntity


@dataclass(frozen=True, kw_only=True)
class HyundaiBluelinkCoverDescription(CoverEntityDescription):
    """Describes a Hyundai Bluelink JP window cover entity."""

    window_position: str


COVER_DESCRIPTIONS: Final[tuple[HyundaiBluelinkCoverDescription, ...]] = (
    HyundaiBluelinkCoverDescription(
        key="front_left_window_is_open",
        translation_key="front_left_window",
        icon="mdi:car-door",
        window_position="front_left",
    ),
    HyundaiBluelinkCoverDescription(
        key="front_right_window_is_open",
        translation_key="front_right_window",
        icon="mdi:car-door",
        window_position="front_right",
    ),
    HyundaiBluelinkCoverDescription(
        key="back_left_window_is_open",
        translation_key="back_left_window",
        icon="mdi:car-door",
        window_position="back_left",
    ),
    HyundaiBluelinkCoverDescription(
        key="back_right_window_is_open",
        translation_key="back_right_window",
        icon="mdi:car-door",
        window_position="back_right",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HyundaiJPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up window and charge-port covers independently."""
    coordinator = config_entry.runtime_data
    entities: list[CoverEntity] = []
    for vehicle_id in coordinator.vehicle_manager.vehicles:
        vehicle: Vehicle = coordinator.vehicle_manager.vehicles[vehicle_id]
        if vehicle.ev_charge_port_door_is_open is not None:
            entities.append(HyundaiBluelinkChargePortCover(coordinator, vehicle))
        if not vehicle.supports.window_control:
            continue
        positions = vehicle.supports.supported_window_positions
        for description in COVER_DESCRIPTIONS:
            if positions is not None and description.window_position not in positions:
                continue
            if getattr(vehicle, description.key, None) is not None:
                entities.append(HyundaiBluelinkCover(coordinator, description, vehicle))

    async_add_entities(entities)


PARALLEL_UPDATES = 1


class HyundaiBluelinkCover(CoverEntity, HyundaiBluelinkEntity):
    """A vehicle window exposed as a cover."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
    )
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JapanDataUpdateCoordinator,
        description: HyundaiBluelinkCoverDescription,
        vehicle: Vehicle,
    ) -> None:
        """Initialize the window cover."""
        HyundaiBluelinkEntity.__init__(self, coordinator, vehicle)
        self.entity_description: HyundaiBluelinkCoverDescription = description
        self._attr_unique_id = f"{DOMAIN}_{vehicle.id}_{description.key}"

    @property
    def is_closed(self) -> bool | None:
        """Return True if the window is closed."""
        is_open = getattr(self.vehicle, self.entity_description.key, None)
        if is_open is None:
            return None
        return not is_open

    @property
    def current_cover_position(self) -> int | None:
        """Return 100 if the window is open, 0 if closed."""
        is_open = getattr(self.vehicle, self.entity_description.key, None)
        if is_open is None:
            return None
        if is_open:
            return 100
        return 0

    def _window_options(self, state: WINDOW_STATE) -> WindowRequestOptions:
        """Options that set only this entity's window to ``state``."""
        options = WindowRequestOptions()
        setattr(options, self.entity_description.window_position, state)
        return options

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the window."""
        options = self._window_options(WINDOW_STATE.OPEN)
        await self.coordinator.async_set_windows(self.vehicle.id, options)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the window."""
        options = self._window_options(WINDOW_STATE.CLOSED)
        await self.coordinator.async_set_windows(self.vehicle.id, options)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """The Japan API has no independent window stop command."""
        raise HomeAssistantError("Remote window stopping is not supported")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the window to closed (0), vent (1-49) or open (50-100)."""
        position = kwargs[ATTR_POSITION]
        if position == 0:
            state = WINDOW_STATE.CLOSED
        elif position <= 49:
            state = WINDOW_STATE.VENTILATION
        else:
            state = WINDOW_STATE.OPEN
        options = self._window_options(state)
        await self.coordinator.async_set_windows(self.vehicle.id, options)


class HyundaiBluelinkChargePortCover(CoverEntity, HyundaiBluelinkEntity):
    """A charging flap with real open/closed and pending movement states."""

    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_translation_key = "charge_port"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: JapanDataUpdateCoordinator, vehicle: Vehicle
    ) -> None:
        HyundaiBluelinkEntity.__init__(self, coordinator, vehicle)
        self._attr_unique_id = f"{DOMAIN}_{vehicle.id}_charge_port"

    @property
    def is_closed(self) -> bool | None:
        value = self.vehicle.ev_charge_port_door_is_open
        return not value if value is not None else None

    @property
    def is_opening(self) -> bool:
        return (
            self.coordinator.get_pending_target(self.vehicle.id, "charge_port") is True
        )

    @property
    def is_closing(self) -> bool:
        return (
            self.coordinator.get_pending_target(self.vehicle.id, "charge_port") is False
        )

    @property
    def icon(self) -> str:
        if self.vehicle.ev_charge_port_door_is_open is None:
            return "mdi:door"
        return (
            "mdi:door-open"
            if self.vehicle.ev_charge_port_door_is_open
            else "mdi:door-closed"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        target = self.coordinator.get_pending_target(self.vehicle.id, "charge_port")
        value = self.vehicle.ev_charge_port_door_is_open
        return {
            "command_pending": target is not None,
            "command_status": self.coordinator.get_pending_status(
                self.vehicle.id, "charge_port"
            ),
            "requested_state": ("open" if target else "closed")
            if target is not None
            else None,
            "last_confirmed_state": ("open" if value else "closed")
            if value is not None
            else None,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_open_charge_port(self.vehicle.id)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_close_charge_port(self.vehicle.id)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        raise HomeAssistantError("Remote charge-port stopping is not supported")
