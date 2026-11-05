"""Actual HA handlers/coordinator schedule changes preserve cloud-owned fields."""

import copy
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.hyundai_jp import services
from custom_components.hyundai_jp._api import Vehicle, VehicleManager
from custom_components.hyundai_jp._api.const import WINDOW_STATE
from custom_components.hyundai_jp._api.exceptions import (
    InvalidAPIResponseError,
    RequestTimeoutError,
)
from custom_components.hyundai_jp._api.options import (
    ScheduleChargingClimateRequestOptions,
)
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp.const import DOMAIN
from custom_components.hyundai_jp.ha_validation import validation as vol
from tests.ha.helpers import make_runtime_coordinator


def _cloud():
    def slot(days, clock, enabled, climate, defrost, temperature):
        return {
            "reservChargeInfoDetail": {
                "reservChargeSet": enabled,
                "reservInfo": {"day": days, "time": {"time": clock, "timeSection": 0}},
                "reservFatcSet": {
                    "airCtrl": climate,
                    "defrost": defrost,
                    "heating1": 2,
                    "airTemp": {"value": temperature, "unit": 0, "hvacTempType": 1},
                },
            }
        }

    return {
        "reservFlag": 1,
        "reservChargeInfo": slot([1, 3], "0830", True, 0, True, "0CH"),
        "reservChargeInfo2": slot([], "0945", False, 1, False, "1AH"),
        "offPeakPowerInfo": {
            "offPeakPowerFlag": 2,
            "offPeakPowerTime1": {
                "starttime": {"time": "1000", "timeSection": 1},
                "endtime": {"time": "0700", "timeSection": 0},
            },
        },
    }


@pytest.fixture
async def environment(monkeypatch, tmp_path):
    hass = HomeAssistant(str(tmp_path))
    manager = VehicleManager(
        "synthetic",
        "synthetic",
        "0000",
        token=Token(
            username="synthetic",
            password="synthetic",
            access_token="synthetic",
        ),
    )
    vehicle = Vehicle(id="car")
    manager.vehicles = {vehicle.id: vehicle}
    api = manager.api
    api._vehicle_profiles = {vehicle.id: {"option": {"hvacTempType": 1}}}
    api._get_control_headers = Mock(return_value={"Authorization": "synthetic"})
    api._vehicle_headers = Mock(return_value={})
    api._mqtt_for_vehicle = Mock(return_value=Mock())
    cloud = _cloud()
    posts = []

    def request(method, path, **kwargs):
        if method == "GET":
            assert path.endswith("/reservation/charge")
            return {"resMsg": copy.deepcopy(cloud)}
        assert method == "POST"
        posts.append((path.rsplit("/", 1)[-1], kwargs["json"]))
        return {"msgId": f"accepted-{len(posts)}"}

    api._request = Mock(side_effect=request)
    owner = make_runtime_coordinator()
    owner.hass = hass
    owner.config_entry = SimpleNamespace(entry_id="entry", unique_id="account")
    owner.vehicle_manager = manager
    owner.async_check_and_refresh_token = AsyncMock()
    owner.async_await_action_and_refresh = AsyncMock()

    async def executor(target, *args):
        return target(*args)

    monkeypatch.setattr(hass, "async_add_executor_job", AsyncMock(side_effect=executor))
    owner.config_entry.runtime_data = owner
    owner.config_entry.state = ConfigEntryState.LOADED
    owner.config_entry.domain = DOMAIN
    hass.config_entries = SimpleNamespace(
        async_entries=lambda domain: [owner.config_entry]
    )
    services.async_setup_services(hass)
    yield SimpleNamespace(
        hass=hass,
        owner=owner,
        manager=manager,
        api=api,
        vehicle=vehicle,
        cloud=cloud,
        posts=posts,
        request=request,
    )
    manager.close()


@pytest.mark.parametrize(
    "method,args",
    [
        ("async_set_schedule_charge_enabled", (False,)),
        ("async_set_off_peak_charge_only_enabled", (False,)),
        ("async_set_departure_enabled", (2, True)),
        ("async_set_departure_climate_enabled", (2, False)),
        ("async_set_departure_defrost", (2, True)),
    ],
)
async def test_schedule_helpers_send_only_requested_changes(environment, method, args):
    e = environment
    before = copy.deepcopy(e.cloud)
    await getattr(e.owner, method)("car", *args)
    payload = e.posts[0][1]
    first, second = payload["reservChargeInfo1"], payload["reservChargeInfo2"]
    assert (
        first["reservInfo"]
        == before["reservChargeInfo"]["reservChargeInfoDetail"]["reservInfo"]
    )
    assert first["reservFatcSet"]["airCtrl"] == 0
    assert first["reservFatcSet"]["defrost"] is True
    assert first["reservFatcSet"]["airTemp"]["value"] == "20.0"
    assert second["reservInfo"]["day"] == []
    assert second["reservInfo"]["time"]["time"] == "0945"
    assert second["reservFatcSet"]["airTemp"]["value"] == "27.0"
    assert second["reservFatcSet"]["airCtrl"] == (
        0 if "climate_enabled" in method else 1
    )
    assert second["reservFatcSet"]["defrost"] is ("defrost" in method)
    assert e.cloud == before


async def test_window_time_change_does_not_change_reservation_flags_or_departures(
    environment,
):
    e = environment
    await e.owner.async_set_off_peak_charging("car", start=dt.time(1, 15))
    payload = e.posts[0][1]
    assert payload["reservFlag"] == e.cloud["reservFlag"]
    assert payload["offPeakPowerInfo"]["offPeakPowerFlag"] == 2
    assert payload["offPeakPowerInfo"]["offPeakPowerTime1"]["starttime"] == {
        "time": "0115",
        "timeSection": 0,
    }
    assert payload["reservChargeInfo2"]["reservInfo"]["day"] == []


async def test_ev5_charge_only_service_skips_unrelated_missing_hvac(environment):
    e = environment
    e.api._vehicle_profiles["car"]["appMode"] = "EV5"
    del e.cloud["reservChargeInfo"]
    del e.cloud["reservChargeInfo2"]
    await e.hass.services.async_call(
        DOMAIN,
        "schedule_charging_and_climate",
        {"charging_enabled": "false"},
        blocking=True,
    )
    assert e.posts == [
        (
            "charge",
            {
                "reservFlag": 0,
                "offpeakPowerFlag": 2,
                "reservStartTime": {"time": "1000", "timeSection": 1},
                "reservEndTime": {"time": "0700", "timeSection": 0},
            },
        )
    ]


async def test_ev5_climate_only_service_preserves_charge_scope_and_empty_days(
    environment,
):
    e = environment
    e.api._vehicle_profiles["car"]["appMode"] = "EV5"
    del e.cloud["offPeakPowerInfo"]
    await e.hass.services.async_call(
        DOMAIN,
        "schedule_charging_and_climate",
        {"first_departure_days": []},
        blocking=True,
    )
    assert [endpoint for endpoint, _ in e.posts] == ["hvac"]
    payload = e.posts[0][1]
    assert payload["reservedHVACInfo1"]["reservInfo"]["day"] == []
    assert payload["reservedHVACInfo2"]["reservHVACSet"]["airTemp"]["value"] == "1AH"


@pytest.mark.parametrize(
    "failure", [RequestTimeoutError("PRIVATE"), InvalidAPIResponseError("PRIVATE")]
)
async def test_ev5_second_submission_failure_reports_partial_acceptance(
    environment, failure
):
    e = environment
    e.api._vehicle_profiles["car"]["appMode"] = "EV5"

    def request(method, path, **kwargs):
        if method == "POST" and path.endswith("/reservation/hvac"):
            raise failure
        return e.request(method, path, **kwargs)

    e.api._request.side_effect = request
    with pytest.raises(
        HomeAssistantError,
        match="partially submitted.*charging accepted.*climate submission outcome is unknown",
    ) as caught:
        await e.hass.services.async_call(
            DOMAIN,
            "schedule_charging_and_climate",
            {"charging_enabled": False, "climate_enabled": False},
            blocking=True,
        )
    assert "PRIVATE" not in str(caught.value)
    assert e.api.get_local_diagnostics()["last_command"]["partial_schedule"] == {
        "accepted_scopes": ["charging"],
        "failed_scope": "climate",
        "submission_outcome": "unknown",
        "vehicle_execution_confirmed": False,
    }
    assert len(e.posts) == 1
    assert sum(call.args[0] == "POST" for call in e.api._request.call_args_list) == 2


async def test_direct_empty_departure_objects_rejected_before_io(environment):
    e = environment
    e.api._request.reset_mock()
    with pytest.raises(ValueError, match="No Japan schedule changes"):
        e.api.schedule_charging_and_climate(
            e.manager.get_token(),
            e.vehicle,
            ScheduleChargingClimateRequestOptions(
                first_departure=ScheduleChargingClimateRequestOptions.DepartureOptions(),
            ),
        )
    e.api._request.assert_not_called()


@pytest.mark.parametrize(
    "service,data",
    [
        ("schedule_charging_and_climate", {}),
        ("schedule_charging_and_climate", {"temperature_unit": 0}),
        ("schedule_charging_and_climate", {"first_departure_time": "07:15:30"}),
        ("schedule_charging_and_climate", {"first_departure_days": [True]}),
        ("schedule_charging_and_climate", {"charging_enabled": "not-a-bool"}),
        ("set_windows", {}),
        ("set_windows", {"flwindow": True}),
        ("set_charge_limits", {"ac_limit": 80}),
        ("set_charge_limits", {"ac_limit": 80, "dc_limit": 85}),
        ("set_charging_current", {"level": 2.5}),
        ("set_navigation", {"latitude": 91, "longitude": 1, "name": "place"}),
        ("set_navigation", {"latitude": 1, "longitude": 1}),
    ],
)
async def test_real_registered_schemas_reject_bad_calls_before_api(
    environment, service, data
):
    e = environment
    with pytest.raises(vol.Invalid):
        await e.hass.services.async_call(DOMAIN, service, data, blocking=True)
    e.api._request.assert_not_called()
    e.owner.async_check_and_refresh_token.assert_not_awaited()


async def test_front_only_windows_work_through_registered_service(environment):
    e = environment
    e.api._vehicle_profiles["car"]["serviceOption"] = {"windowControlOption": 2}
    e.vehicle.supports.window_control = True
    e.vehicle.supports.supported_window_positions = ("front_left", "front_right")
    e.api._vehicle_profiles["car"]["option"]["drvSeatLoc"] = "L"
    e.owner.async_set_windows = AsyncMock(wraps=e.owner.async_set_windows)
    await e.hass.services.async_call(
        DOMAIN, "set_windows", {"flwindow": "1", "frwindow": "1"}, blocking=True
    )
    options = e.owner.async_set_windows.await_args.args[1]
    assert options.front_left is WINDOW_STATE.OPEN
    assert options.front_right is WINDOW_STATE.OPEN
    assert options.back_left is None and options.back_right is None
    # Real API profile validation and payload prove omitted rear fields remain absent.
    assert e.posts == [
        ("windowcurtain", {"drvSeatLoc": "L", "drvSeatWindow": 1, "psgSeatWindow": 1})
    ]


async def test_schedule_fahrenheit_input_is_converted_without_changing_other_slot(
    environment,
):
    e = environment
    await e.hass.services.async_call(
        DOMAIN,
        "schedule_charging_and_climate",
        {"temperature": 68, "temperature_unit": 1},
        blocking=True,
    )
    assert (
        e.posts[0][1]["reservChargeInfo1"]["reservFatcSet"]["airTemp"]["value"]
        == "20.0"
    )
    assert (
        e.posts[0][1]["reservChargeInfo2"]["reservFatcSet"]["airTemp"]["value"]
        == "20.0"
    )
