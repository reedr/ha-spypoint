"""Constants for the Spypoint Trail Cameras integration."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

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
CONF_STALE_MULTIPLIER = "stale_multiplier"
CONF_STALE_MINIMUM = "stale_minimum"
CONF_STALE_MINIMUM_MINUTES = "stale_minimum_minutes"

DEFAULT_STALE_MULTIPLIER = 2.5
DEFAULT_STALE_MINIMUM_SECONDS = 60 * 60
MIN_STALE_MULTIPLIER = 1.5
MAX_STALE_MULTIPLIER = 10.0
MIN_STALE_MINIMUM_SECONDS = 5 * 60
MAX_STALE_MINIMUM_SECONDS = 24 * 60 * 60
STALE_RECHECK_INTERVAL = timedelta(minutes=1)

POSITION_LOCK_STORAGE_VERSION = 1


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


def get_stale_multiplier(config_entry: ConfigEntry) -> float:
    """Return how many expected check-in intervals may pass before stale."""
    if (value := config_entry.options.get(CONF_STALE_MULTIPLIER)) is not None:
        return float(value)
    return DEFAULT_STALE_MULTIPLIER


def get_stale_minimum_seconds(config_entry: ConfigEntry) -> int:
    """Return the floor applied to every camera's staleness threshold."""
    if (value := config_entry.options.get(CONF_STALE_MINIMUM)) is not None:
        return int(value)
    return DEFAULT_STALE_MINIMUM_SECONDS
