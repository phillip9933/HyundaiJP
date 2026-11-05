"""Manager shutdown closes the Japan adapter and existing HTTP resources."""

from unittest.mock import Mock

import pytest
import requests

from custom_components.hyundai_jp._api import VehicleManager
from custom_components.hyundai_jp._api.ApiImpl import ApiImpl
from custom_components.hyundai_jp._api.Vehicle import Vehicle


def manager():
    result = VehicleManager(username="synthetic", password="synthetic", pin="0000")
    result.api.close()
    result.api = Mock(spec=ApiImpl)
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


def test_adapter_closes_unique_owned_sessions_without_opening_lazy_resources():
    class LazyAdapter(ApiImpl):
        @property
        def sessions(self):
            raise AssertionError("shutdown must not create a session")

    api = LazyAdapter()
    primary = Mock(spec=requests.Session)
    service_hub = Mock(spec=requests.Session)
    api.session = primary
    api._service_hub_session = service_hub
    api._sessions = primary

    api.close()

    primary.close.assert_called_once()
    service_hub.close.assert_called_once()


def test_adapter_attempts_all_cleanup_even_if_one_session_fails():
    api = ApiImpl()
    api.session = Mock(spec=requests.Session)
    api._service_hub_session = Mock(spec=requests.Session)
    api._service_hub_session.close.side_effect = RuntimeError("close")

    with pytest.raises(RuntimeError, match="close"):
        api.close()

    api.session.close.assert_called_once()
    api._service_hub_session.close.assert_called_once()


def test_adapter_without_owned_sessions_can_close_repeatedly():
    api = ApiImpl()
    api.session = requests

    api.close()
    api.close()
