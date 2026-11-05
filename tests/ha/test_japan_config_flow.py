"""HyundaiJP setup uses only Japan account credentials."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.hyundai_jp import config_flow
from custom_components.hyundai_jp.config_flow import (
    STEP_CREDENTIALS_DATA_SCHEMA,
    ConfigFlow,
)
from custom_components.hyundai_jp.const import DOMAIN


def flow_fixture(monkeypatch):
    flow = ConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.language = "ja"

    async def executor(fn, *args):
        return fn(*args)

    flow.hass.async_add_executor_job = AsyncMock(side_effect=executor)
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    manager = MagicMock()
    manager.login.return_value = True
    manager.token = None
    constructor = MagicMock(return_value=manager)
    monkeypatch.setattr(config_flow, "VehicleManager", constructor)
    return flow, manager, constructor


async def test_setup_asks_only_for_credentials(monkeypatch):
    flow, _, constructor = flow_fixture(monkeypatch)
    await flow.async_step_user()
    form = flow.async_show_form.call_args.kwargs
    assert DOMAIN == "hyundai_jp"
    assert form["step_id"] == "user"
    assert {key.schema for key in form["data_schema"].schema} == {
        "username",
        "password",
        "pin",
    }
    constructor.assert_not_called()


async def test_login_fixes_japan_and_hyundai_and_releases_temporary_manager(
    monkeypatch,
):
    flow, manager, constructor = flow_fixture(monkeypatch)
    result = await flow.async_step_user(
        {"username": "Synthetic", "password": "synthetic", "pin": "1234"}
    )
    assert result["type"] == "create_entry"
    assert constructor.call_args.kwargs["region"] == 10
    assert constructor.call_args.kwargs["brand"] == 2
    data = flow.async_create_entry.call_args.kwargs
    assert data["title"] == "HyundaiJP Synthetic"
    assert data["data"]["region"] == 10
    assert data["data"]["brand"] == 2
    manager.login.assert_called_once()
    manager.close.assert_called_once()
    flow.async_set_unique_id.assert_awaited_once_with(
        hashlib.sha256(b"synthetic").hexdigest()
    )


async def test_login_preserves_token_for_setup(monkeypatch):
    flow, manager, _ = flow_fixture(monkeypatch)
    manager.token = SimpleNamespace(to_dict=lambda: {"synthetic": "token"})
    await flow.async_step_user(
        {"username": "synthetic", "password": "synthetic", "pin": "1234"}
    )
    assert flow.async_create_entry.call_args.kwargs["data"]["token"] == {
        "synthetic": "token"
    }


@pytest.mark.parametrize("pin", [None, "", "   ", 1234])
async def test_missing_pin_rejected_before_login(monkeypatch, pin):
    flow, _, constructor = flow_fixture(monkeypatch)
    await flow.async_step_user(
        {"username": "synthetic", "password": "synthetic", "pin": pin}
    )
    assert flow.async_show_form.call_args.kwargs["errors"] == {
        "pin": "invalid_japan_pin"
    }
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "failure", [config_flow.AuthenticationError("synthetic"), None]
)
async def test_invalid_login_stays_on_form_and_releases_manager(monkeypatch, failure):
    flow, manager, _ = flow_fixture(monkeypatch)
    if failure:
        manager.login.side_effect = failure
    else:
        manager.login.return_value = False
    await flow.async_step_user(
        {"username": "synthetic", "password": "synthetic", "pin": "1234"}
    )
    assert flow.async_show_form.call_args.kwargs["errors"] == {"base": "invalid_auth"}
    manager.close.assert_called_once()


def test_pin_schema_is_required_without_default():
    with pytest.raises(vol.MultipleInvalid):
        STEP_CREDENTIALS_DATA_SCHEMA({"username": "synthetic", "password": "synthetic"})
    marker = next(k for k in STEP_CREDENTIALS_DATA_SCHEMA.schema if k.schema == "pin")
    assert isinstance(marker, vol.Required)
    assert marker.default is vol.UNDEFINED


async def test_reconfigure_pin_updates_saved_token(monkeypatch):
    flow, _, _ = flow_fixture(monkeypatch)
    entry = SimpleNamespace(data={"token": {"pin": "1234", "value": "synthetic"}})
    flow._get_reconfigure_entry = MagicMock(return_value=entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
    await flow.async_step_reconfigure_pin({"pin": "5678"})
    assert flow.async_update_reload_and_abort.call_args.kwargs["data_updates"] == {
        "pin": "5678",
        "token": {"pin": "5678", "value": "synthetic"},
    }
