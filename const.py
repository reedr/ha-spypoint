"""Constants for the Spypoint Trail Cameras integration."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

DOMAIN = "spypoint"
MANUFACTURER = "Spypoint"

API_BASE = "https://restapi.spypoint.com/api/v3"
PATH_LOGIN = "/user/login"
PATH_CAMERA_ALL = "/camera/all"
PATH_PHOTO_ALL = "/photo/all"
PATH_CAMERA_SETTINGS = "/camera/settings"
PATH_CAMERA_COMMAND = "/camera/command"
PATH_PHOTO_VIDEO = "/photo/video"

SERVICE_GET_PHOTOS = "get_photos"
SERVICE_REQUEST_HDVIDEO = "request_hdvideo"

EVENT_PHOTO_COUNT_CHANGED = f"{DOMAIN}_photo_count_changed"

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
MIN_UPDATE_INTERVAL_SECONDS = 60
MAX_UPDATE_INTERVAL_SECONDS = 24 * 60 * 60
REFRESH_ALIGN_OFFSET = timedelta(minutes=5)
REFRESH_ALIGN_INTERVAL = timedelta(minutes=30)

CONF_SCAN_INTERVAL = "scan_interval"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"


def get_update_interval_seconds(config_entry: ConfigEntry) -> int:
    """Return the configured coordinator update interval in seconds."""
    if (interval := config_entry.options.get(CONF_SCAN_INTERVAL)) is not None:
        return int(interval)
    return DEFAULT_UPDATE_INTERVAL_SECONDS


def should_align_update_interval(interval_seconds: int) -> bool:
    """Return True when refresh should align to clock times."""
    return interval_seconds % int(REFRESH_ALIGN_INTERVAL.total_seconds()) == 0


def next_aligned_update_time(now: datetime, interval_seconds: int) -> datetime:
    """Return the next refresh time aligned to local midnight + 5 minutes."""
    interval = timedelta(seconds=interval_seconds)
    anchor = dt_util.start_of_local_day(now) + REFRESH_ALIGN_OFFSET

    if now < anchor:
        return anchor

    elapsed = now - anchor
    return anchor + (elapsed // interval + 1) * interval
