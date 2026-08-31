"""Diagnostics support for the Spypoint Trail Cameras integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .camera_data import camera_name, last_update, photo_count, photo_limit
from .const import DOMAIN
from .coordinator import SpypointConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "attributes",
    "coordinates",
    "email",
    "geohash",
    "latitude",
    "longitude",
    "position",
    "refresh_token",
    "token",
    "ucid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SpypointConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    cameras: dict[str, dict[str, Any]] = (coordinator.data or {}).get("cameras", {})
    known_ids = set(cameras)
    locks = coordinator.position_lock_state

    device_registry = dr.async_get(hass)
    registry_devices = dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    )

    registered: dict[str, str] = {}
    for device in registry_devices:
        for domain, identifier in device.identifiers:
            if domain == DOMAIN:
                registered[identifier] = device.name_by_user or device.name or ""

    orphaned_devices = sorted(set(registered) - known_ids)
    orphaned_locks = sorted(set(locks) - known_ids)

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "camera_count": len(cameras),
        },
        "orphans": {
            "devices_not_in_api": [
                {"camera_id": camera_id, "name": registered[camera_id]}
                for camera_id in orphaned_devices
            ],
            "position_locks_not_in_api": orphaned_locks,
        },
        "cameras": async_redact_data(
            {
                camera_id: {
                    "name": camera_name(camera),
                    "photo_count": photo_count(camera),
                    "photo_limit": photo_limit(camera),
                    "last_update": (
                        value.isoformat()
                        if (value := last_update(camera)) is not None
                        else None
                    ),
                    "position_locked": coordinator.is_position_locked(camera_id),
                    "in_device_registry": camera_id in registered,
                    "raw": camera,
                }
                for camera_id, camera in cameras.items()
            },
            TO_REDACT,
        ),
        "position_locks": async_redact_data(locks, TO_REDACT),
        "generated_at": dt_util.utcnow().isoformat(),
    }
