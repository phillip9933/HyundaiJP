"""Replay Japan HTTP acceptance through HA without any MQTT result packets."""

import json
import time
from unittest.mock import Mock

from homeassistant.exceptions import HomeAssistantError
import pytest
import requests

from custom_components.hyundai_jp._api import Token, Vehicle, VehicleManager
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.const import ORDER_STATUS
from tests.ha.helpers import make_operation_coordinator


def pipeline(body=None, failure=None):
    api = HyundaiBluelinkApi()
    helper = Mock()
    helper.request_context = {
        "clientId": "synthetic-client",
        "vehicleId": "synthetic-broker",
    }
    helper.status.side_effect = AssertionError(
        "ordinary controls must not wait for MQTT"
    )
    helper.track.side_effect = AssertionError(
        "ordinary controls must not track MQTT result"
    )
    api._mqtt_for_vehicle = Mock(return_value=helper)
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(
        body
        if body is not None
        else {
            "retCode": "S",
            "resCode": "0000",
            "msgId": "synthetic-action",
        }
    ).encode()
    api.session.request = Mock(side_effect=failure, return_value=response)
    manager = VehicleManager.__new__(VehicleManager)
    manager.api = api
    manager.token = Token(
        username="synthetic",
        password="synthetic",
        access_token="Bearer synthetic-access",
        device_id="synthetic-device",
        pin="synthetic-pin",
        mqtt_registration_uuid="synthetic-uuid",
        control_token="Bearer synthetic-control",
        control_token_expiry=time.time() + 3600,
    )
    vehicle = Vehicle(id="synthetic-car", ccu_ccs2_protocol_support=1, is_locked=True)
    manager.vehicles = {vehicle.id: vehicle}
    manager.update_all_vehicles_with_cached_state = Mock()
    result = make_operation_coordinator()
    result.vehicle_manager = manager
    result._start_post_command_state_watch = Mock()

    async def executor(fn, *args):
        return fn(*args)

    result.hass.async_add_executor_job = executor
    return result, api, manager, helper, vehicle


@pytest.mark.parametrize(
    "action,command",
    [
        ("async_unlock_vehicle", "open"),
        ("async_stop_climate", "stop"),
    ],
)
async def test_service_result_accepted_with_no_mqtt_reply(action, command):
    result, api, manager, helper, vehicle = pipeline()
    await getattr(result, action)(vehicle.id)
    request = api.session.request.call_args
    assert request.kwargs["json"] == {"command": command}
    assert request.kwargs["timeout"] == (10, 140)
    assert api.session.request.call_count == 1
    helper.status.assert_not_called()
    helper.track.assert_not_called()
    receipt = next(iter(api._japan_actions))
    assert manager.check_action_status(vehicle.id, receipt) == ORDER_STATUS.ACCEPTED
    assert (
        vehicle.is_locked is True
    )  # HTTP acceptance never fabricates a sensor change.
    manager.update_all_vehicles_with_cached_state.assert_called_once()
    result._start_post_command_state_watch.assert_called_once_with(vehicle.id)
    result.async_set_updated_data.assert_called_once()
    assert not result._action_lock.locked()


async def test_service_result_does_not_require_mqtt_correlation_id():
    result, api, manager, helper, vehicle = pipeline(
        {"retCode": "S", "resCode": "0000"}
    )
    await result.async_unlock_vehicle(vehicle.id)
    receipt = next(iter(api._japan_actions))
    assert manager.check_action_status(vehicle.id, receipt) == ORDER_STATUS.ACCEPTED
    helper.status.assert_not_called()
    assert api.session.request.call_count == 1


async def test_accepted_command_still_watches_when_initial_cached_read_fails():
    result, api, manager, _helper, vehicle = pipeline()
    manager.update_all_vehicles_with_cached_state.side_effect = RuntimeError(
        "synthetic cached read failure"
    )
    with pytest.raises(HomeAssistantError, match="service accepted the request"):
        await result.async_unlock_vehicle(vehicle.id)
    result._start_post_command_state_watch.assert_called_once_with(vehicle.id)
    assert api.session.request.call_count == 1
    assert vehicle.is_locked is True
    assert not result._action_lock.locked()


async def test_http_timeout_is_unknown_and_does_not_resend():
    result, api, _manager, helper, vehicle = pipeline(
        failure=requests.Timeout("synthetic")
    )
    with pytest.raises(HomeAssistantError, match="vehicle execution is unknown"):
        await result.async_unlock_vehicle(vehicle.id)
    assert api.session.request.call_count == 1
    assert not getattr(api, "_japan_actions", {})
    helper.status.assert_not_called()
    result._start_post_command_state_watch.assert_called_once_with(vehicle.id)
    assert not result._action_lock.locked()


async def test_vendor_error_is_not_accepted_or_retried():
    result, api, _manager, helper, vehicle = pipeline(
        {"retCode": "F", "resCode": "4002"}
    )
    with pytest.raises(HomeAssistantError, match="invalid request"):
        await result.async_unlock_vehicle(vehicle.id)
    assert api.session.request.call_count == 1
    assert not getattr(api, "_japan_actions", {})
    helper.status.assert_not_called()
    result._start_post_command_state_watch.assert_not_called()


@pytest.mark.parametrize("code", ["4002", "4004", "5031", "9998"])
async def test_explicit_service_rejection_clears_pending_without_replay(code):
    result, api, _manager, _helper, vehicle = pipeline(
        {"retCode": "F", "resCode": code}
    )
    with pytest.raises(HomeAssistantError):
        await result.async_unlock_vehicle(vehicle.id)
    assert result.get_pending_target(vehicle.id, "door_lock") is None
    assert result.get_pending_status(vehicle.id, "door_lock") == "rejected"
    assert api.session.request.call_count == 1
    result._start_post_command_state_watch.assert_not_called()


async def test_pin_timeout_before_action_does_not_leave_unlock_pending():
    from custom_components.hyundai_jp._api.exceptions import RequestTimeoutError

    result, api, _manager, _helper, vehicle = pipeline()
    api._get_control_headers = Mock(
        side_effect=RequestTimeoutError("synthetic PIN timeout")
    )
    with pytest.raises(HomeAssistantError, match="could not be submitted"):
        await result.async_unlock_vehicle(vehicle.id)
    assert result.get_pending_target(vehicle.id, "door_lock") is None
    api.session.request.assert_not_called()
    api._mqtt_for_vehicle.assert_not_called()
    result._start_post_command_state_watch.assert_not_called()


async def test_http_408_keeps_bounded_unknown_progress():
    result, api, _manager, _helper, vehicle = pipeline({})
    api.session.request.return_value.status_code = 408
    with pytest.raises(HomeAssistantError):
        await result.async_unlock_vehicle(vehicle.id)
    assert result.get_pending_target(vehicle.id, "door_lock") is False
    assert result.get_pending_status(vehicle.id, "door_lock") == "submission_unknown"
    assert api.session.request.call_count == 1
    result._start_post_command_state_watch.assert_called_once_with(vehicle.id)
    assert not result._action_lock.locked()
