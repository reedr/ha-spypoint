"""Config flow for the Spypoint Trail Cameras integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_INSTANT_SCAN_INTERVAL,
    CONF_INSTANT_SCAN_INTERVAL_MINUTES,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_STALE_MINIMUM,
    CONF_STALE_MINIMUM_MINUTES,
    CONF_STALE_MULTIPLIER,
    DOMAIN,
    MAX_STALE_MINIMUM_SECONDS,
    MAX_STALE_MULTIPLIER,
    MAX_UPDATE_INTERVAL_SECONDS,
    MIN_STALE_MINIMUM_SECONDS,
    MIN_STALE_MULTIPLIER,
    MIN_UPDATE_INTERVAL_SECONDS,
    get_instant_update_interval_seconds,
    get_stale_minimum_seconds,
    get_stale_multiplier,
    get_update_interval_seconds,
)
from .device import SpypointAuthError, SpypointConnectionError, SpypointDevice

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate credentials and return account metadata."""
    device = SpypointDevice(hass, data[CONF_USERNAME], data[CONF_PASSWORD])
    try:
        await device.async_setup()
        await device.login()
        cameras = await device.get_cameras()
    finally:
        await device.async_close()
    if not device.user_id:
        raise CannotConnect
    return {
        "title": device.account_title or data[CONF_USERNAME],
        "unique_id": device.user_id,
        "cameras": len(cameras),
    }


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Spypoint Trail Cameras."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except SpypointAuthError:
                errors["base"] = "invalid_auth"
            except (CannotConnect, SpypointConnectionError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            data = {
                CONF_USERNAME: reauth_entry.data[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                info = await validate_input(self.hass, data)
            except SpypointAuthError:
                errors["base"] = "invalid_auth"
            except (CannotConnect, SpypointConnectionError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during reauthentication")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlow):
    """Handle Spypoint options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Spypoint options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL_MINUTES] * 60,
                    CONF_INSTANT_SCAN_INTERVAL: (
                        user_input[CONF_INSTANT_SCAN_INTERVAL_MINUTES] * 60
                    ),
                    CONF_STALE_MULTIPLIER: user_input[CONF_STALE_MULTIPLIER],
                    CONF_STALE_MINIMUM: user_input[CONF_STALE_MINIMUM_MINUTES] * 60,
                }
            )

        current_minutes = get_update_interval_seconds(self.config_entry) // 60
        current_instant_minutes = (
            get_instant_update_interval_seconds(self.config_entry) // 60
        )
        current_stale_multiplier = get_stale_multiplier(self.config_entry)
        current_stale_minimum_minutes = (
            get_stale_minimum_seconds(self.config_entry) // 60
        )
        interval_range = vol.All(
            vol.Coerce(int),
            vol.Range(
                min=MIN_UPDATE_INTERVAL_SECONDS // 60,
                max=MAX_UPDATE_INTERVAL_SECONDS // 60,
            ),
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES,
                    default=current_minutes,
                ): interval_range,
                vol.Required(
                    CONF_INSTANT_SCAN_INTERVAL_MINUTES,
                    default=current_instant_minutes,
                ): interval_range,
                vol.Required(
                    CONF_STALE_MULTIPLIER,
                    default=current_stale_multiplier,
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(min=MIN_STALE_MULTIPLIER, max=MAX_STALE_MULTIPLIER),
                ),
                vol.Required(
                    CONF_STALE_MINIMUM_MINUTES,
                    default=current_stale_minimum_minutes,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_STALE_MINIMUM_SECONDS // 60,
                        max=MAX_STALE_MINIMUM_SECONDS // 60,
                    ),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
