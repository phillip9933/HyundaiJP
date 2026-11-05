"""Each Japan account owns its command receipts, camera cache and observations."""

from unittest.mock import Mock

import pytest

from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.const import ORDER_STATUS
from custom_components.hyundai_jp._api.exceptions import NoDataFound
from custom_components.hyundai_jp._api.svm import SVMDetails
from custom_components.hyundai_jp._api.Token import Token
from custom_components.hyundai_jp._api.Vehicle import Vehicle


def test_two_accounts_sharing_vehicle_id_do_not_share_receipts_or_camera_data():
    first, second = HyundaiBluelinkApi(), HyundaiBluelinkApi()
    vehicle = Vehicle(id="synthetic-car")
    token = Token(username="synthetic", password="synthetic")
    for client in (first, second):
        client.session.request = Mock(side_effect=AssertionError("local work only"))
    try:
        first._remember_japan_action(
            "synthetic-action", vehicle.id, status=ORDER_STATUS.ACCEPTED
        )
        image = SVMDetails(image_bytes=b"synthetic-camera")
        first._japan_svm_cache[vehicle.id] = image
        first._record_passive_state_result("applied")

        assert (
            first.check_action_status(token, vehicle, "synthetic-action")
            is ORDER_STATUS.ACCEPTED
        )
        assert (
            second.check_action_status(token, vehicle, "synthetic-action")
            is ORDER_STATUS.UNKNOWN
        )
        assert first.get_svm_details(token, vehicle) is image
        with pytest.raises(NoDataFound):
            second.get_svm_details(token, vehicle)
        assert first.get_local_diagnostics()["passive_state"]["applied"] == 1
        other = second.get_local_diagnostics()
        assert other["svm_images_cached"] == 0
        assert other["last_command"] == {}
        assert other["last_refresh"] == {}
        assert other["last_camera_capture"] == {"stage": "idle"}
        assert "passive_state" not in other
        for client in (first, second):
            client.session.request.assert_not_called()
    finally:
        first.close()
        second.close()
