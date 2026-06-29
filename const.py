"""Constants for the Spypoint Trail Cameras integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .camera_data import is_instant_mode, transmit_freq

DOMAIN = "spypoint"
MANUFACTURER = "Spypoint"

API_BASE = "https://restapi.spypoint.com/api/v3"
PATH_LOGIN = "/user/login"
PATH_CAMERA = "/camera"
PATH_CAMERA_ALL = "/camera/all"
PATH_PHOTO_ALL = "/photo/all"
PATH_CAMERA_SETTINGS = "/camera/settings"
PATH_CAMERA_COMMAND = "/camera/command"
PATH_PHOTO_VIDEO = "/photo/video"

SERVICE_GET_PHOTOS = "get_photos"
SERVICE_REQUEST_HDVIDEO = "request_hdvideo"
SERVICE_REFRESH_CAMERAS = "refresh_cameras"

EVENT_NEW_PHOTOS = f"{DOMAIN}_new_photos"

ATTR_DEVICE_ID = "device_id"
ATTR_CAMERA_ID = "camera_id"
ATTR_PHOTO_ID = "photo_id"
ATTR_DATE_START = "date_start"
ATTR_DATE_END = "date_end"
ATTR_LIMIT = "limit"
ATTR_MEDIA_TYPES = "media_types"
ATTR_SPECIES = "species"

MEDIA_TYPES = frozenset({"hdphoto", "hdvideo", "preview"})
SPECIES = frozenset(
    {
        "buck",
        "bear",
        "coyote",
        "deer",
        "humanactivity",
        "moose",
        "turkey",
        "wildboar",
    }
)

DEFAULT_PHOTOS_LIMIT = 20
DEFAULT_PHOTOS_LOOKBACK_DAYS = 7
DEFAULT_UPDATE_INTERVAL_SECONDS = 30 * 60
DEFAULT_INSTANT_UPDATE_INTERVAL_SECONDS = 60
COORDINATOR_UPDATE_INTERVAL_SECONDS = 60
SECONDS_PER_DAY = 24 * 60 * 60
MIN_UPDATE_INTERVAL_SECONDS = 60
MAX_UPDATE_INTERVAL_SECONDS = 24 * 60 * 60
REFRESH_ALIGN_OFFSET = timedelta(minutes=5)

CONF_SCAN_INTERVAL = "scan_interval"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_INSTANT_SCAN_INTERVAL = "instant_scan_interval"
CONF_INSTANT_SCAN_INTERVAL_MINUTES = "instant_scan_interval_minutes"


def aligned_refresh_anchor(now: datetime) -> datetime:
    """Return the daily anchor at local midnight plus five minutes."""
    return dt_util.start_of_local_day(now) + REFRESH_ALIGN_OFFSET


def current_aligned_slot(now: datetime, interval_seconds: int) -> datetime | None:
    """Return the aligned refresh slot at or before now."""
    anchor = aligned_refresh_anchor(now)
    if now < anchor:
        return None
    interval = timedelta(seconds=interval_seconds)
    elapsed = now - anchor
    return anchor + (elapsed // interval) * interval


def should_aligned_refresh(
    last_refresh: datetime | None, now: datetime, interval_seconds: int
) -> bool:
    """Return True when a new aligned refresh slot has started."""
    if last_refresh is None:
        return True
    if (slot := current_aligned_slot(now, interval_seconds)) is None:
        return False
    return last_refresh < slot


def get_update_interval_seconds(config_entry: ConfigEntry) -> int:
    """Return the configured full camera refresh interval in seconds."""
    if (interval := config_entry.options.get(CONF_SCAN_INTERVAL)) is not None:
        return int(interval)
    return DEFAULT_UPDATE_INTERVAL_SECONDS


def get_instant_update_interval_seconds(config_entry: ConfigEntry) -> int:
    """Return the configured instant camera refresh interval in seconds."""
    if (interval := config_entry.options.get(CONF_INSTANT_SCAN_INTERVAL)) is not None:
        return int(interval)
    return DEFAULT_INSTANT_UPDATE_INTERVAL_SECONDS


def camera_status_refresh_interval_seconds(
    camera: dict[str, Any], config_entry: ConfigEntry
) -> int:
    """Return the aligned status refresh interval for a camera in seconds."""
    if is_instant_mode(camera):
        return get_instant_update_interval_seconds(config_entry)
    if (freq := transmit_freq(camera)) is not None and freq > 0:
        return SECONDS_PER_DAY // freq
    return get_update_interval_seconds(config_entry)
