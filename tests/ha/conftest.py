"""Shared pytest configuration for hyundai_jp tests."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from custom_components.hyundai_jp import (
    binary_sensor as binary_sensor_platform,
    sensor as sensor_platform,
)
from custom_components.hyundai_jp._api import Vehicle
from tests.ha.helpers import make_coordinator, make_hass


@pytest.fixture
def setup_sensors() -> Callable[..., Awaitable[dict[str, Any]]]:
    """Run the real sensor ``async_setup_entry`` for the given vehicles.

    Returns the created entities that have an ``entity_description``, keyed
    by description key.
    """

    async def _setup(*vehicles: Vehicle) -> dict[str, Any]:
        hass, config_entry = make_hass(make_coordinator(*vehicles))
        created: list[Any] = []
        await sensor_platform.async_setup_entry(hass, config_entry, created.extend)
        return {
            entity.entity_description.key: entity
            for entity in created
            if getattr(entity, "entity_description", None) is not None
        }

    return _setup


@pytest.fixture
def setup_binary_sensors() -> Callable[..., Awaitable[dict[str, Any]]]:
    """Run the real binary_sensor ``async_setup_entry`` for the given vehicles.

    Returns the created entities keyed by description key.
    """

    async def _setup(*vehicles: Vehicle) -> dict[str, Any]:
        hass, config_entry = make_hass(make_coordinator(*vehicles))
        created: list[Any] = []
        await binary_sensor_platform.async_setup_entry(
            hass, config_entry, created.extend
        )
        return {entity.entity_description.key: entity for entity in created}

    return _setup
