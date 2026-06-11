"""Services for Spypoint Trail Cameras."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_CAMERA_ID,
    ATTR_DATE_END,
    ATTR_DATE_START,
    ATTR_DEVICE_ID,
    ATTR_LIMIT,
    ATTR_PHOTO_ID,
    DEFAULT_PHOTOS_LIMIT,
    DEFAULT_PHOTOS_LOOKBACK_DAYS,
    DOMAIN,
    SERVICE_GET_PHOTOS,
    SERVICE_REQUEST_HDVIDEO,
)
from .coordinator import SpypointCoordinator
from .device import SpypointAuthError, SpypointConnectionError

SERVICE_GET_PHOTOS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_DATE_START): cv.datetime,
        vol.Optional(ATTR_DATE_END): cv.datetime,
        vol.Optional(ATTR_LIMIT, default=DEFAULT_PHOTOS_LIMIT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
    }
)


SERVICE_REQUEST_HDVIDEO_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_CAMERA_ID): cv.string,
        vol.Required(ATTR_PHOTO_ID): cv.string,
    }
)


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> SpypointCoordinator:
    """Return the coordinator for a config entry."""
    if not (entry := hass.config_entries.async_get_entry(entry_id)):
        raise ServiceValidationError(f"Config entry '{entry_id}' not found")
    if entry.domain != DOMAIN:
        raise ServiceValidationError(f"Config entry '{entry_id}' is not a Spypoint entry")
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(f"Config entry '{entry_id}' is not loaded")
    return entry.runtime_data


def _resolve_camera_id_from_device(
    hass: HomeAssistant, entry_id: str, device_id: str
) -> str:
    """Return the Spypoint API camera id for a Home Assistant device."""
    device_registry = dr.async_get(hass)
    if not (device := device_registry.async_get(device_id)):
        raise ServiceValidationError(f"Unknown device_id '{device_id}'")
    if entry_id not in device.config_entries:
        raise ServiceValidationError(
            f"Device '{device_id}' does not belong to config entry '{entry_id}'"
        )

    for identifier_domain, camera_id in device.identifiers:
        if identifier_domain == DOMAIN:
            return camera_id

    raise ServiceValidationError(
        f"Device '{device_id}' is not a Spypoint camera device"
    )


async def _resolve_camera_ids(
    hass: HomeAssistant,
    coordinator: SpypointCoordinator,
    entry_id: str,
    device_id: str | None,
) -> list[str]:
    """Return Spypoint API camera ids to query."""
    if device_id:
        return [_resolve_camera_id_from_device(hass, entry_id, device_id)]

    if coordinator.data and (cameras := coordinator.data.get("cameras")):
        return list(cameras)

    cameras_list = await coordinator.device.get_cameras()
    return [camera["id"] for camera in cameras_list if camera.get("id")]


def _resolve_date_range(
    date_start: datetime | None, date_end: datetime | None
) -> tuple[datetime, datetime]:
    """Return the date range for a photo query."""
    if date_end is None:
        date_end = datetime.now(tz=UTC)
    elif date_end.tzinfo is None:
        date_end = date_end.replace(tzinfo=UTC)

    if date_start is None:
        date_start = date_end - timedelta(days=DEFAULT_PHOTOS_LOOKBACK_DAYS)
    elif date_start.tzinfo is None:
        date_start = date_start.replace(tzinfo=UTC)

    if date_start > date_end:
        raise ServiceValidationError("date_start must be before date_end")

    return date_start, date_end


async def async_get_photos(call: ServiceCall) -> ServiceResponse:
    """Fetch photo metadata from Spypoint."""
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    coordinator = _get_coordinator(call.hass, entry_id)
    camera_ids = await _resolve_camera_ids(
        call.hass,
        coordinator,
        entry_id,
        call.data.get(ATTR_DEVICE_ID),
    )

    if not camera_ids:
        return {"photos": [], "count": 0, "camera_ids": []}

    date_start, date_end = _resolve_date_range(
        call.data.get(ATTR_DATE_START), call.data.get(ATTR_DATE_END)
    )

    try:
        photos = await coordinator.device.get_photos(
            camera_ids,
            limit=call.data[ATTR_LIMIT],
            date_start=date_start,
            date_end=date_end,
        )
    except SpypointAuthError as err:
        raise HomeAssistantError("Spypoint authentication failed") from err
    except SpypointConnectionError as err:
        raise HomeAssistantError("Unable to fetch photos from Spypoint") from err

    return {
        "photos": photos,
        "count": len(photos),
        "camera_ids": camera_ids,
    }


async def async_request_hdvideo(call: ServiceCall) -> ServiceResponse:
    """Request an HD video/photo from Spypoint."""
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    coordinator = _get_coordinator(call.hass, entry_id)
    camera_id = call.data[ATTR_CAMERA_ID]
    photo_id = call.data[ATTR_PHOTO_ID]

    try:
        result = await coordinator.device.async_request_hd_video(camera_id, photo_id)
    except SpypointAuthError as err:
        raise HomeAssistantError("Spypoint authentication failed") from err
    except SpypointConnectionError as err:
        raise HomeAssistantError("Unable to request HD video from Spypoint") from err

    return {"result": result}


async def async_setup(hass: HomeAssistant) -> None:
    """Register Spypoint services."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PHOTOS,
        async_get_photos,
        schema=SERVICE_GET_PHOTOS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_HDVIDEO,
        async_request_hdvideo,
        schema=SERVICE_REQUEST_HDVIDEO_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
