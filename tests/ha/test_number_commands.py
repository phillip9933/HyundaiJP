"""Slider commands preserve other limits and never coerce malformed targets."""

from unittest.mock import AsyncMock, Mock

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp import number
from custom_components.hyundai_jp._api import Vehicle
from tests.ha.helpers import make_coordinator, make_hass


def entity(key, *, ac=80, dc=90, v2l=30):
    vehicle = Vehicle(
        id="chosen",
        ev_charge_limits_ac=ac,
        ev_charge_limits_dc=dc,
        ev_v2l_discharge_limit=v2l,
    )
    coordinator = make_coordinator(vehicle, Vehicle(id="other"))
    coordinator.async_set_charge_limits = AsyncMock()
    coordinator.async_set_v2l_limit = AsyncMock()
    description = next(item for item in number.NUMBER_DESCRIPTIONS if item.key == key)
    result = number.HyundaiBluelinkNumber(coordinator, description, vehicle)
    result.async_write_ha_state = Mock()
    return result


async def test_setup_creates_only_reported_limits_and_reads_latest_telemetry():
    first = Vehicle(id="first", ev_charge_limits_ac=80)
    second = Vehicle(id="second", ev_charge_limits_dc=90, ev_v2l_discharge_limit=20)
    hass, entry = make_hass(make_coordinator(first, second))
    entities = []
    await number.async_setup_entry(hass, entry, entities.extend)
    assert {(item.vehicle.id, item.entity_description.key) for item in entities} == {
        ("first", number.AC_CHARGING_LIMIT_KEY),
        ("second", number.DC_CHARGING_LIMIT_KEY),
        ("second", number.V2L_LIMIT_KEY),
    }
    ac = next(item for item in entities if item.vehicle is first)
    assert ac.native_value == 80
    first.ev_charge_limits_ac = 70
    assert ac.native_value == 70
    first.ev_charge_limits_ac = None
    assert ac.native_value is None


@pytest.mark.parametrize(
    "key,expected",
    [
        (number.AC_CHARGING_LIMIT_KEY, (70, 90)),
        (number.DC_CHARGING_LIMIT_KEY, (80, 70)),
    ],
)
async def test_charge_slider_preserves_the_other_latest_limit_without_claiming_success(
    key, expected
):
    result = entity(key)
    await result.async_set_native_value(70)
    result.coordinator.async_set_charge_limits.assert_awaited_once_with(
        "chosen", *expected
    )
    result.coordinator.async_set_v2l_limit.assert_not_called()
    assert result.vehicle.ev_charge_limits_ac == 80
    assert result.vehicle.ev_charge_limits_dc == 90
    result.async_write_ha_state.assert_called_once()


@pytest.mark.parametrize(
    "key,current",
    [(number.AC_CHARGING_LIMIT_KEY, 80), (number.DC_CHARGING_LIMIT_KEY, 90)],
)
async def test_same_limit_does_not_send_a_duplicate_command(key, current):
    result = entity(key)
    await result.async_set_native_value(float(current))
    result.coordinator.async_set_charge_limits.assert_not_called()
    result.async_write_ha_state.assert_not_called()


@pytest.mark.parametrize(
    "key", [number.AC_CHARGING_LIMIT_KEY, number.DC_CHARGING_LIMIT_KEY]
)
@pytest.mark.parametrize(
    "invalid",
    [None, True, 80.5, float("nan"), float("inf"), -float("inf"), "80", 10**1000],
)
async def test_unknown_or_invalid_other_limit_never_becomes_an_invented_paired_target(
    key, invalid
):
    result = entity(
        key,
        **({"dc": invalid} if key == number.AC_CHARGING_LIMIT_KEY else {"ac": invalid}),
    )
    with pytest.raises(HomeAssistantError, match="not available yet"):
        await result.async_set_native_value(70)
    result.coordinator.async_set_charge_limits.assert_not_called()
    result.async_write_ha_state.assert_not_called()


@pytest.mark.parametrize(
    "key",
    [number.AC_CHARGING_LIMIT_KEY, number.DC_CHARGING_LIMIT_KEY, number.V2L_LIMIT_KEY],
)
@pytest.mark.parametrize(
    "invalid",
    [
        True,
        False,
        80.5,
        float("nan"),
        float("inf"),
        -float("inf"),
        "70",
        10**1000,
        0,
        110,
    ],
)
async def test_invalid_slider_targets_fail_before_conversion_or_submission(
    key, invalid
):
    result = entity(key)
    with pytest.raises(HomeAssistantError, match="supported whole-number limit"):
        await result.async_set_native_value(invalid)
    result.coordinator.async_set_charge_limits.assert_not_called()
    result.coordinator.async_set_v2l_limit.assert_not_called()
    result.async_write_ha_state.assert_not_called()


@pytest.mark.parametrize("limit", [20, 80])
async def test_v2l_slider_preserves_supported_boundaries(limit):
    result = entity(number.V2L_LIMIT_KEY)
    await result.async_set_native_value(limit)
    result.coordinator.async_set_v2l_limit.assert_awaited_once_with("chosen", limit)
    result.coordinator.async_set_charge_limits.assert_not_called()
    assert result.native_value == 30


@pytest.mark.parametrize("key", [number.AC_CHARGING_LIMIT_KEY, number.V2L_LIMIT_KEY])
async def test_rejected_limit_does_not_publish_an_optimistic_state_or_replay(key):
    result = entity(key)
    error = HomeAssistantError("Vehicle is busy")
    action = (
        result.coordinator.async_set_v2l_limit
        if key == number.V2L_LIMIT_KEY
        else result.coordinator.async_set_charge_limits
    )
    action.side_effect = error
    before = result.native_value
    with pytest.raises(HomeAssistantError) as caught:
        await result.async_set_native_value(70)
    assert caught.value is error
    action.assert_awaited_once()
    result.async_write_ha_state.assert_not_called()
    assert result.native_value == before
