"""The Spypoint Trail Cameras integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigType
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Spypoint services."""
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpypointConfigEntry) -> bool:
    """Set up Spypoint Trail Cameras from a config entry."""
    device = SpypointDevice(
        hass,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = SpypointCoordinator(hass, entry, device)
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpypointConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.runtime_data is not None:
        await entry.runtime_data.device.async_close()
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

    camera_ids = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    }
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
