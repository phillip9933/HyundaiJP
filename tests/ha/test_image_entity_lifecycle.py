"""SVM image entities serve owned cached views and release their subscriptions."""

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest

from custom_components.hyundai_jp import image
from custom_components.hyundai_jp._api import Vehicle
from custom_components.hyundai_jp.coordinator import SIGNAL_SVM_RENDER
from tests.ha.helpers import make_coordinator


@pytest.fixture
async def environment(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    first, second, unsupported = (
        Vehicle(id="first"),
        Vehicle(id="second"),
        Vehicle(id="unsupported"),
    )
    first.supports.svm = second.supports.svm = True
    coordinator = make_coordinator(first, second, unsupported)
    coordinator.hass = hass
    coordinator.get_svm_image_updated_at.return_value = None
    coordinator.get_cached_svm_details.return_value = None
    coordinator.async_get_svm_views = AsyncMock(return_value=None)
    entities = []
    await image.async_setup_entry(
        hass, SimpleNamespace(runtime_data=coordinator), entities.extend
    )
    for index, entity in enumerate(entities):
        entity.hass = hass
        entity.entity_id = f"image.synthetic_{index}"
        entity.async_write_ha_state = Mock()
    yield SimpleNamespace(hass=hass, coordinator=coordinator, entities=entities)
    # ImageEntity's proxy client belongs to HA, not to an individual camera.
    await hass.async_stop(force=True)
    assert all(entity._client.is_closed for entity in entities)


async def test_image_setup_creates_supported_views_without_fetching_or_fabricating_capture(
    environment,
):
    e = environment
    assert len(e.entities) == 10
    assert len({entity.unique_id for entity in e.entities}) == 10
    for entity in e.entities:
        assert entity.vehicle.id in ("first", "second")
        assert not entity.available
        assert entity.image_last_updated is None
        assert entity.content_type == "image/jpeg"
        assert entity.access_tokens
    e.coordinator.async_get_svm_views.assert_not_awaited()
    e.coordinator.async_supports_svm.assert_not_awaited()


async def test_image_reads_only_its_vehicle_and_view_and_preserves_missing_output(
    environment,
):
    e = environment
    entity = e.entities[0]
    assert await entity.async_image() is None
    e.coordinator.async_get_svm_views.assert_awaited_once_with("first")
    e.coordinator.async_get_svm_views.return_value = {"rear": b"other-view"}
    assert await entity.async_image() is None
    e.coordinator.async_get_svm_views.return_value = {
        "front": b"chosen-jpeg",
        "rear": b"other-view",
    }
    assert await entity.async_image() == b"chosen-jpeg"
    assert all(
        call.args == ("first",)
        for call in e.coordinator.async_get_svm_views.await_args_list
    )


async def test_image_render_failure_propagates_once_without_inventing_a_picture(
    environment,
):
    e = environment
    entity = e.entities[0]
    error = HomeAssistantError("Camera image could not be rendered")
    e.coordinator.async_get_svm_views.side_effect = error
    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_image()
    assert caught.value is error
    e.coordinator.async_get_svm_views.assert_awaited_once_with("first")
    assert entity.image_last_updated is None
    entity.async_write_ha_state.assert_not_called()


async def test_capture_updates_cached_timestamp_and_availability_without_new_commands(
    environment,
):
    e = environment
    entity = e.entities[0]
    captured = dt.datetime(2026, 11, 6, tzinfo=dt.UTC)
    assert entity.image_last_updated is None
    e.coordinator.get_svm_image_updated_at.return_value = captured
    e.coordinator.get_cached_svm_details.return_value = object()
    entity._handle_coordinator_update()
    assert entity.available
    assert entity.image_last_updated == captured
    e.coordinator.async_get_svm_views.assert_not_awaited()
    entity.async_write_ha_state.assert_called_once()


async def test_render_signal_is_vehicle_scoped_and_unsubscribed_on_entity_removal(
    environment, monkeypatch
):
    e = environment
    first, second = e.entities[0], e.entities[5]
    first_listener_removed = Mock()
    e.coordinator.async_add_listener.return_value = first_listener_removed
    await first.async_added_to_hass()
    await second.async_added_to_hass()
    rendered_at = dt.datetime(2026, 11, 6, 1, tzinfo=dt.UTC)
    monkeypatch.setattr(image.dt_util, "now", lambda: rendered_at)

    async_dispatcher_send(e.hass, SIGNAL_SVM_RENDER.format("first"))
    await e.hass.async_block_till_done()
    assert first.image_last_updated == rendered_at
    first.async_write_ha_state.assert_called_once()
    second.async_write_ha_state.assert_not_called()
    assert second.image_last_updated is None

    await first.async_remove(force_remove=True)
    first_listener_removed.assert_called_once()
    first.async_write_ha_state.reset_mock()
    async_dispatcher_send(e.hass, SIGNAL_SVM_RENDER.format("first"))
    await e.hass.async_block_till_done()
    first.async_write_ha_state.assert_not_called()
    await second.async_remove(force_remove=True)
    assert first_listener_removed.call_count == 2
