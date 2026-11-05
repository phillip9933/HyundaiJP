"""Config flow for Hyundai Bluelink JP integration."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import selector

from ._api import VehicleManager
from ._api.exceptions import AuthenticationError
from .const import (
    CONF_ENABLE_GEOLOCATION_ENTITY,
    CONF_FORCE_REFRESH_INTERVAL,
    CONF_NO_FORCE_REFRESH_HOUR_FINISH,
    CONF_NO_FORCE_REFRESH_HOUR_START,
    CONF_TOKEN,
    CONF_USE_EMAIL_WITH_GEOCODE_API,
    DEFAULT_ENABLE_GEOLOCATION_ENTITY,
    DEFAULT_FORCE_REFRESH_INTERVAL,
    DEFAULT_NO_FORCE_REFRESH_HOUR_FINISH,
    DEFAULT_NO_FORCE_REFRESH_HOUR_START,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_EMAIL_WITH_GEOCODE_API,
    DOMAIN,
)
from .ha_validation import Schema, validation as vol

_LOGGER = logging.getLogger(__name__)


def _account_unique_id(username: str) -> str:
    """Use the same account identity for setup, reauth and reconfigure."""
    return hashlib.sha256(username.strip().casefold().encode("utf-8")).hexdigest()


STEP_RECONFIGURE_DATA_SCHEMA = Schema(
    {
        vol.Required("reconfigure_choice"): selector(
            {
                "select": {
                    "options": [
                        {"value": "reauth", "label": "Re-authenticate your account"},
                        {"value": "pin", "label": "Set / change PIN"},
                    ],
                    "mode": "list",
                }
            }
        ),
    }
)

STEP_PIN_ONLY_SCHEMA = Schema(
    {
        vol.Required(CONF_PIN): vol.All(str, vol.Length(min=1)),
    }
)

STEP_CREDENTIALS_DATA_SCHEMA = Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): selector({"text": {"type": "password"}}),
        vol.Required(CONF_PIN): vol.All(str, vol.Length(min=1)),
    }
)

OPTIONS_SCHEMA = Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=15, max=999)
        ),
        vol.Required(
            CONF_FORCE_REFRESH_INTERVAL,
            default=DEFAULT_FORCE_REFRESH_INTERVAL,
        ): vol.All(vol.Coerce(int), vol.Range(min=90, max=9999)),
        vol.Required(
            CONF_NO_FORCE_REFRESH_HOUR_START,
            default=DEFAULT_NO_FORCE_REFRESH_HOUR_START,
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required(
            CONF_NO_FORCE_REFRESH_HOUR_FINISH,
            default=DEFAULT_NO_FORCE_REFRESH_HOUR_FINISH,
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Optional(
            CONF_ENABLE_GEOLOCATION_ENTITY,
            default=DEFAULT_ENABLE_GEOLOCATION_ENTITY,
        ): bool,
        vol.Optional(
            CONF_USE_EMAIL_WITH_GEOCODE_API,
            default=DEFAULT_USE_EMAIL_WITH_GEOCODE_API,
        ): bool,
    }
)


async def validate_input(
    hass: HomeAssistant,
    vehicle_manager: VehicleManager,
) -> bool:
    """Validate the user input allows us to connect."""
    try:
        result = await hass.async_add_executor_job(vehicle_manager.login)

        if not result:
            raise InvalidAuth

        return True
    except AuthenticationError as err:
        raise InvalidAuth from err


class HyundaiBluelinkOptionFlowHandler(config_entries.OptionsFlowWithReload):
    """Handle an option flow for Hyundai Bluelink JP."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options init setup."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.config_entry.title, data=user_input
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hyundai Bluelink JP."""

    VERSION = 1
    reauth_entry: ConfigEntry | None = None

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._is_reconfigure = False

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> HyundaiBluelinkOptionFlowHandler:
        """Initiate options flow instance."""
        return HyundaiBluelinkOptionFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in to a Hyundai Japan Bluelink account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            target_entry = (
                self._get_reconfigure_entry()
                if self._is_reconfigure
                else self.reauth_entry
            )
            if (
                target_entry is not None
                and _account_unique_id(user_input[CONF_USERNAME])
                != target_entry.unique_id
            ):
                # Reauthentication updates an existing account, not its identity.
                # Reject before opening a temporary client or sending credentials.
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_CREDENTIALS_DATA_SCHEMA,
                    errors={"base": "wrong_account"},
                )
            pin = user_input.get(CONF_PIN)
            if not isinstance(pin, str) or not pin.strip():
                errors[CONF_PIN] = "invalid_japan_pin"
            else:
                full_config = dict(user_input)
                vehicle_manager = VehicleManager(
                    username=full_config[CONF_USERNAME],
                    password=full_config[CONF_PASSWORD],
                    pin=pin,
                )
                try:
                    await validate_input(self.hass, vehicle_manager)
                    if vehicle_manager.token is not None:
                        full_config[CONF_TOKEN] = vehicle_manager.token.to_dict()
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except Exception as err:
                    _LOGGER.error("Login failed (%s)", type(err).__name__[:64])
                    errors["base"] = "unknown"
                else:
                    if self._is_reconfigure:
                        return self.async_update_reload_and_abort(
                            self._get_reconfigure_entry(), data_updates=full_config
                        )
                    if self.reauth_entry is not None:
                        return self.async_update_reload_and_abort(
                            self.reauth_entry,
                            data_updates=full_config,
                            reason="reauth_successful",
                        )
                    title = f"Hyundai Bluelink JP {user_input[CONF_USERNAME]}"
                    await self.async_set_unique_id(
                        _account_unique_id(user_input[CONF_USERNAME])
                    )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=title, data=full_config)
                finally:
                    await self.hass.async_add_executor_job(vehicle_manager.close)
        return self.async_show_form(
            step_id="user", data_schema=STEP_CREDENTIALS_DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Present choice: re-authenticate or set/change PIN."""
        if user_input is not None:
            if user_input["reconfigure_choice"] == "reauth":
                self._is_reconfigure = True
                return await self.async_step_user()
            else:
                return await self.async_step_reconfigure_pin()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=STEP_RECONFIGURE_DATA_SCHEMA,
        )

    async def async_step_reconfigure_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to set or change the PIN without full re-authentication."""
        if user_input is not None:
            new_pin = user_input[CONF_PIN]
            if not isinstance(new_pin, str) or not new_pin.strip():
                return self.async_show_form(
                    step_id="reconfigure_pin",
                    data_schema=STEP_PIN_ONLY_SCHEMA,
                    errors={CONF_PIN: "invalid_japan_pin"},
                )
            data_updates: dict[str, Any] = {CONF_PIN: new_pin}
            entry = self._get_reconfigure_entry()
            token_data = entry.data.get(CONF_TOKEN)
            if token_data is not None:
                updated_token = {**token_data, CONF_PIN: new_pin}
                data_updates[CONF_TOKEN] = updated_token
            return self.async_update_reload_and_abort(
                entry,
                data_updates=data_updates,
            )

        return self.async_show_form(
            step_id="reconfigure_pin",
            data_schema=STEP_PIN_ONLY_SCHEMA,
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_user()


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
