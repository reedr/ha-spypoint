"""The Spypoint Trail Cameras integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigType
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .device import SpypointDevice
from .services import async_setup as async_setup_services

_PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON, Platform.IMAGE]


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
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
