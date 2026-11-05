"""Japan capabilities control which vehicle commands appear in HA."""

from custom_components.hyundai_jp import button, climate, lock, number, switch
from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.coordinator import JapanDataUpdateCoordinator
from tests.ha.helpers import make_coordinator, make_hass


async def test_japan_entities_follow_vehicle_profile_and_telemetry():
    vehicle = Vehicle(id="synthetic", name="synthetic", model="synthetic")
    vehicle.air_control_is_on = False
    vehicle.ev_battery_is_charging = False
    vehicle.ev_charge_limits_ac = 80
    vehicle.ev_charge_limits_dc = 90
    vehicle.supports.svm = True
    coordinator = make_coordinator(vehicle)
    coordinator.__class__ = JapanDataUpdateCoordinator
    hass, entry = make_hass(coordinator)
    entities = {}
    for platform in (lock, climate, number, switch, button):
        created = []
        await platform.async_setup_entry(
            hass, entry, lambda values, *args, target=created: target.extend(values)
        )
        entities[platform.__name__.rsplit(".", 1)[-1]] = created
    assert len(entities["lock"]) == 1
    assert len(entities["climate"]) == 1
    assert {e.entity_description.key for e in entities["number"]} == {
        "ev_charge_limits_ac",
        "ev_charge_limits_dc",
    }
    assert {e.entity_description.key for e in entities["button"]} == {
        "force_refresh",
        "start_hazard_lights",
        "start_hazard_lights_and_horn",
        "capture_svm_image",
    }
    assert any(isinstance(e, switch.SVMDewarpSwitch) for e in entities["switch"])
