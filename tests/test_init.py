"""Integration setup: services and the bundled gallery card."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.spypoint import CARD_PATH, CARD_URL

VERSION = json.loads((CARD_PATH.parent.parent / "manifest.json").read_text())["version"]


async def test_card_served_and_registered(hass: HomeAssistant) -> None:
    assert CARD_PATH.is_file()
    with patch(
        "homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths",
        new_callable=AsyncMock,
    ) as register:
        assert await async_setup_component(hass, "spypoint", {})
        await hass.async_block_till_done()

    paths = [p for call in register.call_args_list for p in call.args[0]]
    assert (CARD_URL, str(CARD_PATH)) in [(p.url_path, p.path) for p in paths]
    resources = hass.data[LOVELACE_DATA].resources.async_items()
    assert [(r["type"], r["url"]) for r in resources] == [("module", f"{CARD_URL}?v={VERSION}")]
    assert hass.services.has_service("spypoint", "get_photos")
    assert hass.services.has_service("spypoint", "request_hdvideo")


async def test_card_resource_updated(hass: HomeAssistant) -> None:
    """An older version's resource is updated in place; duplicates are removed."""
    assert await async_setup_component(hass, "lovelace", {})
    resources = hass.data[LOVELACE_DATA].resources
    await resources.async_get_info()
    await resources.async_create_item({"res_type": "module", "url": "/local/other-card.js"})
    await resources.async_create_item({"res_type": "js", "url": f"{CARD_URL}?v=0.1.0"})
    await resources.async_create_item({"res_type": "module", "url": f"{CARD_URL}?v=0.2.0"})
    with patch(
        "homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths",
        new_callable=AsyncMock,
    ):
        assert await async_setup_component(hass, "spypoint", {})
        await hass.async_block_till_done()
    assert sorted((r["type"], r["url"]) for r in resources.async_items()) == [
        ("module", "/local/other-card.js"),
        ("module", f"{CARD_URL}?v={VERSION}"),
    ]
