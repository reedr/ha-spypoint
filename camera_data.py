"""Shared camera data extraction helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .device import parse_spypoint_timestamp


def camera_name(camera: dict[str, Any]) -> str:
    """Return the display name for a camera."""
    config = camera.get("config") or {}
    status = camera.get("status") or {}
    return config.get("name") or status.get("model") or camera.get("id", "Spypoint Camera")


def photo_count(camera: dict[str, Any]) -> int | None:
    """Extract photos used in the current billing cycle."""
    subscriptions = camera.get("subscriptions") or []
    if not subscriptions:
        return None
    if (value := subscriptions[0].get("photoCount")) is None:
        return None
    return int(value)


def photo_limit(camera: dict[str, Any]) -> int | None:
    """Extract monthly photo limit."""
    subscriptions = camera.get("subscriptions") or []
    if not subscriptions:
        return None
    if (value := subscriptions[0].get("photoLimit")) is None:
        return None
    return int(value)


def last_update(camera: dict[str, Any]) -> datetime | None:
    """Extract the last camera status update time."""
    status = camera.get("status") or {}
    return parse_spypoint_timestamp(status.get("lastUpdate"))
