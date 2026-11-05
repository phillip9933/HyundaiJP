"""Manager shutdown closes the Japan adapter and existing HTTP resources."""

from unittest.mock import Mock

from custom_components.hyundai_jp._api import VehicleManager
from custom_components.hyundai_jp._api.client import HyundaiBluelinkApi
from custom_components.hyundai_jp._api.Vehicle import Vehicle


def manager():
    result = VehicleManager(username="synthetic", password="synthetic", pin="0000")
    result.api.close()
    result.api = Mock(spec=HyundaiBluelinkApi)
    result.vehicles = {"synthetic": Vehicle(id="synthetic")}
    return result


def test_close_is_idempotent():
    fleet = manager()

    fleet.close()
    fleet.close()

    fleet.api.close.assert_called_once()


def test_close_can_reenter_from_adapter_cleanup():
    fleet = manager()
    fleet.api.close.side_effect = fleet.close

    fleet.close()

    fleet.api.close.assert_called_once()
