"""Integration setup: services and the bundled gallery card."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.spypoint import CARD_PATH, CARD_URL


async def test_card_served_and_registered(hass: HomeAssistant) -> None:
    assert CARD_PATH.is_file()
    with (
        patch(
            "homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths",
            new_callable=AsyncMock,
        ) as register,
        patch("custom_components.spypoint.add_extra_js_url") as add_js,
    ):
        assert await async_setup_component(hass, "spypoint", {})
        await hass.async_block_till_done()

    paths = [p for call in register.call_args_list for p in call.args[0]]
    assert (CARD_URL, str(CARD_PATH)) in [(p.url_path, p.path) for p in paths]
    assert add_js.call_args.args[1] == f"{CARD_URL}?v=0.2.1"
    assert hass.services.has_service("spypoint", "get_photos")
    assert hass.services.has_service("spypoint", "request_hdvideo")
