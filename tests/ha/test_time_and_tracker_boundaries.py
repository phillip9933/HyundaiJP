"""Time controls and location entities preserve routing and unknown data."""

import datetime as dt
from unittest.mock import AsyncMock, Mock

from homeassistant.components.device_tracker.const import SourceType
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp import device_tracker, time
from custom_components.hyundai_jp._api import Vehicle
from tests.ha.helpers import make_coordinator, make_hass


async def test_time_setup_exposes_only_reported_fields():
    first, second = Vehicle(id="first"), Vehicle(id="second")
    first.ev_off_peak_start_time = dt.time(23, 30)
    second.ev_off_peak_end_time = dt.time(6, 15)
    coordinator = make_coordinator(first, second)
    hass, entry = make_hass(coordinator)
    entities = []
    await time.async_setup_entry(hass, entry, entities.extend)
    assert {
        (entity.vehicle.id, entity.entity_description.key) for entity in entities
    } == {("first", "ev_off_peak_start_time"), ("second", "ev_off_peak_end_time")}
    assert [entity.native_value for entity in entities] == [
        dt.time(23, 30),
        dt.time(6, 15),
    ]
    first.ev_off_peak_start_time = dt.time(22, 45)
    assert entities[0].native_value == dt.time(22, 45)
    first.ev_off_peak_start_time = None
    assert entities[0].native_value is None

    second.ev_off_peak_end_time = None
    entities.clear()
    await time.async_setup_entry(hass, entry, entities.extend)
    assert entities == []


@pytest.mark.parametrize(
    "key,field", [("ev_off_peak_start_time", "start"), ("ev_off_peak_end_time", "end")]
)
async def test_time_control_routes_only_its_vehicle_and_requested_field(key, field):
    vehicle = Vehicle(id="chosen")
    coordinator = make_coordinator(vehicle, Vehicle(id="other"))
    coordinator.async_set_off_peak_charging = AsyncMock()
    description = next(item for item in time.TIME_DESCRIPTIONS if item.key == key)
    entity = time.HyundaiBluelinkTimeEntity(coordinator, description, vehicle)
    entity.async_write_ha_state = Mock()
    await entity.async_set_value(dt.time(7, 15))
    coordinator.async_set_off_peak_charging.assert_awaited_once_with(
        "chosen", **{field: dt.time(7, 15)}
    )
    assert entity.native_value is None
    coordinator.async_set_off_peak_charging.side_effect = HomeAssistantError("rejected")
    entity.async_write_ha_state.reset_mock()
    with pytest.raises(HomeAssistantError, match="rejected"):
        await entity.async_set_value(dt.time(8, 30))
    entity.async_write_ha_state.assert_not_called()
    assert entity.native_value is None


async def test_tracker_uses_current_latitude_longitude_and_keeps_missing_values_unknown():
    vehicle = Vehicle(id="known")
    vehicle.location = (35.5, 139.5, dt.datetime(2026, 11, 5, tzinfo=dt.UTC))
    missing = Vehicle(id="missing")
    coordinator = make_coordinator(vehicle, missing)
    hass, entry = make_hass(coordinator)
    entities = []
    await device_tracker.async_setup_entry(hass, entry, entities.extend)
    trackers = {entity.vehicle.id: entity for entity in entities}
    known = trackers["known"]
    assert (known.latitude, known.longitude) == (35.5, 139.5)
    assert known.source_type == SourceType.GPS
    assert known.unique_id != trackers["missing"].unique_id
    assert (trackers["missing"].latitude, trackers["missing"].longitude) == (None, None)
    vehicle.location = (0, 0, dt.datetime(2026, 11, 5, tzinfo=dt.UTC))
    assert (known.latitude, known.longitude) == (0, 0)
    vehicle.location = (None, None, None)
    assert (known.latitude, known.longitude) == (None, None)
