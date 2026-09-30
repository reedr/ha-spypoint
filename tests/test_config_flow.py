"""Config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spypoint.device import SpypointAuthError, SpypointConnectionError

DATA = {"username": "user@example.com", "password": "secret"}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init("spypoint", context={"source": SOURCE_USER})


async def test_user_flow(hass: HomeAssistant, mock_account) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.spypoint.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["result"].unique_id == "user-1"


async def test_user_flow_errors(hass: HomeAssistant, mock_account) -> None:
    result = await _start(hass)
    mock_account["login_error"] = SpypointAuthError("bad")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "invalid_auth"}
    mock_account["login_error"] = SpypointConnectionError("down")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, mock_account) -> None:
    MockConfigEntry(domain="spypoint", unique_id="user-1", data=DATA).add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
