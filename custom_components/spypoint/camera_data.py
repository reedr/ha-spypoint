"""Shared camera data extraction helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    SECONDS_PER_DAY,
    get_instant_update_interval_seconds,
    get_stale_minimum_seconds,
    get_stale_multiplier,
    get_update_interval_seconds,
)
from .device import parse_spypoint_timestamp


def is_instant_mode(camera: dict[str, Any]) -> bool:
    """Return True when the camera should use instant refresh polling."""
    config = camera.get("config") or {}
    return config.get("operationMode") == "instant" or transmit_freq(camera) == 0


def camera_name(camera: dict[str, Any]) -> str:
    """Return the display name for a camera."""
    config = camera.get("config") or {}
    status = camera.get("status") or {}
    return config.get("name") or status.get("model") or camera.get("id", "Spypoint Camera")


def _int(data: Any, key: str) -> int | None:
    """Read an integer field, tolerating odd API data (it feeds every update)."""
    if not isinstance(data, dict) or (value := data.get(key)) is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def photo_count(camera: dict[str, Any]) -> int | None:
    """Extract photos used in the current billing cycle."""
    subscriptions = camera.get("subscriptions") or []
    if not subscriptions:
        return None
    return _int(subscriptions[0], "photoCount")


def photo_limit(camera: dict[str, Any]) -> int | None:
    """Extract monthly photo limit."""
    subscriptions = camera.get("subscriptions") or []
    if not subscriptions:
        return None
    return _int(subscriptions[0], "photoLimit")


def transmit_freq(camera: dict[str, Any]) -> int | None:
    """Extract cloud transmit frequency (connections per day, 0 = every detection)."""
    config = camera.get("config") or {}
    if (value := config.get("transmitFreq")) is None:
        return None
    return int(value)


def camera_status_refresh_interval_seconds(
    camera: dict[str, Any], config_entry: ConfigEntry
) -> int:
    """Return the aligned status refresh interval for a camera in seconds."""
    if is_instant_mode(camera):
        return get_instant_update_interval_seconds(config_entry)
    if (freq := transmit_freq(camera)) is not None and freq > 0:
        return SECONDS_PER_DAY // freq
    return get_update_interval_seconds(config_entry)


def camera_stale_after_seconds(
    camera: dict[str, Any], config_entry: ConfigEntry
) -> int:
    """Return how long a camera may go without checking in before it is stale.

    Derived from the camera's own transmit frequency, so a camera that reports
    six times a day is given proportionally more slack than one reporting
    twelve, with a configurable floor for very frequent reporters.
    """
    expected = camera_status_refresh_interval_seconds(camera, config_entry)
    threshold = int(expected * get_stale_multiplier(config_entry))
    return max(threshold, get_stale_minimum_seconds(config_entry))


def last_update(camera: dict[str, Any]) -> datetime | None:
    """Extract the last camera status update time."""
    status = camera.get("status") or {}
    return parse_spypoint_timestamp(status.get("lastUpdate"))


def new_photo_count_for_event(
    camera: dict[str, Any], *, new_count: int, previous_count: int
) -> int:
    """Return the new photo count to include in the event payload."""
    if new_count < previous_count:
        count = new_count
    else:
        count = new_count - previous_count

    config = camera.get("config") or {}
    if config.get("captureMode") == "video":
        return count // 3
    return count
