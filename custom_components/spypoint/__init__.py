"""The Spypoint Trail Cameras integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .device import SpypointDevice
from .services import async_setup as async_setup_services

_LOGGER = logging.getLogger(__name__)

_PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.DEVICE_TRACKER,
    Platform.SWITCH,
]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_URL = "/spypoint/spypoint-gallery-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "spypoint-gallery-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Spypoint services and the gallery card."""
    await async_setup_services(hass)

    # Serve the card from the integration so HACS updates it along with the code.
    # The version query makes browsers fetch the new file after an update.
    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    await _async_register_card(hass, f"{CARD_URL}?v={integration.version}")
    return True


async def _async_register_card(hass: HomeAssistant, url: str) -> None:
    """Load the card on every dashboard, including in the Companion apps.

    A dashboard resource is what the mobile apps reliably load (they can run a
    cached frontend page that predates extra_js_url injection), so keep one
    resource pointing at the current version. With YAML-mode resources the
    collection can't be written, so fall back to injecting the module.
    """
    lovelace = hass.data.get(LOVELACE_DATA)
    resources = getattr(lovelace, "resources", None)
    if lovelace is None or lovelace.resource_mode != "storage" or resources is None:
        add_extra_js_url(hass, url)
        return
    await resources.async_get_info()  # loads the collection
    ours = [item for item in resources.async_items() if item["url"].split("?")[0] == CARD_URL]
    if not ours:
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("Added dashboard resource %s", url)
        return
    first, *extra = ours
    if first["url"] != url or first.get("type") != "module":
        await resources.async_update_item(first["id"], {"res_type": "module", "url": url})
        _LOGGER.info("Updated dashboard resource to %s", url)
    for item in extra:
        await resources.async_delete_item(item["id"])


async def async_setup_entry(hass: HomeAssistant, entry: SpypointConfigEntry) -> bool:
    """Set up Spypoint Trail Cameras from a config entry."""
    device = SpypointDevice(
        hass,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    # Registered first so it also runs when setup fails, and after the
    # coordinator's own shutdown.
    entry.async_on_unload(device.async_close)
    coordinator = SpypointCoordinator(hass, entry, device)
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: SpypointConfigEntry) -> None:
    """Reload once the new options are saved."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SpypointConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: SpypointConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removal of a device that is no longer returned by the cloud API.

    Cameras are keyed in the device registry by their Spypoint camera id. A
    device may be deleted by the user once that id is missing from the latest
    successful update, which happens when the camera has been removed from the
    Spypoint account.
    """
    coordinator: SpypointCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        # No trustworthy view of the account; refuse rather than orphan a
        # camera that is still active.
        return False

    camera_ids = {identifier for domain, identifier in device_entry.identifiers if domain == DOMAIN}
    if not camera_ids:
        # Device carries no identifier owned by this integration.
        return True

    if camera_ids & coordinator.camera_ids:
        # Between discovery refreshes the cached camera list can be stale, so
        # confirm against the API before refusing the removal.
        await coordinator.async_refresh_camera_list()

    if camera_ids & coordinator.camera_ids:
        _LOGGER.debug(
            "Refusing removal of Spypoint device %s; still returned by the API",
            device_entry.id,
        )
        return False

    _LOGGER.debug(
        "Allowing removal of orphaned Spypoint device %s (camera ids: %s)",
        device_entry.id,
        ", ".join(sorted(camera_ids)),
    )
    return True
