"""Button for Hyundai Bluelink JP integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._api import Vehicle
from .const import DOMAIN
from .coordinator import HyundaiJPConfigEntry, JapanDataUpdateCoordinator
from .entity import HyundaiBluelinkEntity


@dataclass(frozen=True, kw_only=True)
class HyundaiBluelinkButtonDescription(ButtonEntityDescription):
    """Describes a Hyundai Bluelink JP button entity."""

    press_action: str
    exists_fn: Callable[[Vehicle], bool] = lambda _: True
    enabled_fn: Callable[[Vehicle], bool] = lambda _: True
    icon_fn: Callable[[Vehicle], str | None] | None = None


BUTTON_DESCRIPTIONS: Final[tuple[HyundaiBluelinkButtonDescription, ...]] = (
    HyundaiBluelinkButtonDescription(
        key="charge_port_toggle",
        translation_key="charge_port_toggle",
        press_action="async_toggle_charge_port",
        exists_fn=lambda vehicle: vehicle.ev_charge_port_door_is_open is not None,
        icon_fn=lambda vehicle: (
            "mdi:door"
            if vehicle.ev_charge_port_door_is_open is None
            else "mdi:door-open"
            if vehicle.ev_charge_port_door_is_open
            else "mdi:door-closed"
        ),
    ),
    HyundaiBluelinkButtonDescription(
        key="force_refresh",
        translation_key="force_refresh",
        icon="mdi:refresh",
        press_action="async_force_refresh_vehicle",
    ),
    HyundaiBluelinkButtonDescription(
        key="start_hazard_lights",
        translation_key="start_hazard_lights",
        icon="mdi:hazard-lights",
        press_action="async_start_hazard_lights",
        enabled_fn=lambda _: False,
    ),
    HyundaiBluelinkButtonDescription(
        key="start_hazard_lights_and_horn",
        translation_key="start_hazard_lights_and_horn",
        icon="mdi:car-emergency",
        press_action="async_start_hazard_lights_and_horn",
        enabled_fn=lambda _: False,
    ),
    HyundaiBluelinkButtonDescription(
        key="start_valet_mode",
        translation_key="start_valet_mode",
        icon="mdi:key-variant",
        press_action="async_start_valet_mode",
        exists_fn=lambda vehicle: bool(vehicle.supports.valet_mode),
    ),
    HyundaiBluelinkButtonDescription(
        key="stop_valet_mode",
        translation_key="stop_valet_mode",
        icon="mdi:key-variant",
        press_action="async_stop_valet_mode",
        exists_fn=lambda vehicle: bool(vehicle.supports.valet_mode),
    ),
    HyundaiBluelinkButtonDescription(
        key="open_all_windows",
        translation_key="open_all_windows",
        icon="mdi:window-maximize",
        press_action="async_open_all_windows",
        exists_fn=lambda vehicle: (
            bool(vehicle.supports.window_control)
            and vehicle.front_left_window_is_open is not None
        ),
    ),
    HyundaiBluelinkButtonDescription(
        key="close_all_windows",
        translation_key="close_all_windows",
        icon="mdi:window-minimize",
        press_action="async_close_all_windows",
        exists_fn=lambda vehicle: (
            bool(vehicle.supports.window_control)
            and vehicle.front_left_window_is_open is not None
        ),
    ),
    HyundaiBluelinkButtonDescription(
        key="vent_all_windows",
        translation_key="vent_all_windows",
        icon="mdi:window-open-variant",
        press_action="async_vent_all_windows",
        exists_fn=lambda vehicle: (
            bool(vehicle.supports.window_control)
            and vehicle.front_left_window_is_open is not None
        ),
    ),
    HyundaiBluelinkButtonDescription(
        key="capture_svm_image",
        translation_key="capture_svm_image",
        icon="mdi:camera-iris",
        press_action="async_request_svm_capture",
        exists_fn=lambda vehicle: bool(vehicle.supports.svm),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HyundaiJPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    coordinator = config_entry.runtime_data
    entities = []
    for vehicle_id in coordinator.vehicle_manager.vehicles:
        vehicle: Vehicle = coordinator.vehicle_manager.vehicles[vehicle_id]
        for description in BUTTON_DESCRIPTIONS:
            if not description.exists_fn(vehicle):
                continue
            entities.append(HyundaiBluelinkButton(coordinator, description, vehicle))

    async_add_entities(entities)


PARALLEL_UPDATES = 1


class HyundaiBluelinkButton(ButtonEntity, HyundaiBluelinkEntity):
    """Button that triggers a vehicle command on press."""

    def __init__(
        self,
        coordinator: JapanDataUpdateCoordinator,
        description: HyundaiBluelinkButtonDescription,
        vehicle: Vehicle,
    ) -> None:
        """Initialize the button."""
        HyundaiBluelinkEntity.__init__(self, coordinator, vehicle)
        self.entity_description: HyundaiBluelinkButtonDescription = description
        self._key = description.key
        self._attr_unique_id = f"{DOMAIN}_{vehicle.id}_{self._key}"
        self._attr_icon = description.icon
        self._attr_entity_registry_enabled_default = description.enabled_fn(vehicle)

    @property
    def icon(self) -> str | None:
        """Use current vehicle state for buttons with a stateful icon."""
        icon_fn = self.entity_description.icon_fn
        return icon_fn(self.vehicle) if icon_fn is not None else self._attr_icon

    async def async_press(self) -> None:
        """Run the coordinator action bound to this button."""
        await getattr(self.coordinator, self.entity_description.press_action)(
            self.vehicle.id
        )
