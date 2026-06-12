"""Constants for the Spypoint Trail Cameras integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry

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

ATTR_DEVICE_ID = "device_id"
ATTR_CAMERA_ID = "camera_id"
ATTR_PHOTO_ID = "photo_id"
ATTR_DATE_START = "date_start"
ATTR_DATE_END = "date_end"
ATTR_LIMIT = "limit"

DEFAULT_PHOTOS_LIMIT = 20
DEFAULT_PHOTOS_LOOKBACK_DAYS = 7
DEFAULT_UPDATE_INTERVAL_SECONDS = 30 * 60
MIN_UPDATE_INTERVAL_SECONDS = 60
MAX_UPDATE_INTERVAL_SECONDS = 24 * 60 * 60

CONF_SCAN_INTERVAL = "scan_interval"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"


def get_update_interval_seconds(config_entry: ConfigEntry) -> int:
    """Return the configured coordinator update interval in seconds."""
    if (interval := config_entry.options.get(CONF_SCAN_INTERVAL)) is not None:
        return int(interval)
    return DEFAULT_UPDATE_INTERVAL_SECONDS
