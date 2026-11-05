"""Base Entity for HyundaiJP integration."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._api import Vehicle
from .const import DOMAIN
from .japan_coordinator import JapanDataUpdateCoordinator


class HyundaiKiaConnectEntity(CoordinatorEntity[JapanDataUpdateCoordinator]):
    """Class for base entity for HyundaiJP integration."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JapanDataUpdateCoordinator,
        vehicle: Vehicle,
    ) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self.vehicle = vehicle

    @property
    def available(self) -> bool:
        """Return False when this vehicle's own poll failed."""
        return super().available and self.coordinator.vehicle_available(self.vehicle.id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to use for this entity."""
        identifiers = {(DOMAIN, self.vehicle.id)}
        if vin := self.vehicle.VIN:
            # Hyundai may replace its internal vehicle id after an account or
            # subscription migration. The VIN keeps HA attached to the same car.
            identifiers.add((DOMAIN, f"vin:{vin}"))
        return DeviceInfo(
            identifiers=identifiers,
            manufacturer="Hyundai Japan",
            model=self.vehicle.model,
            name=self.vehicle.name,
            serial_number=f"{self.vehicle.VIN}",
        )
