"""Spypoint cloud API device."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from json.decoder import JSONDecodeError
import logging
import re
from typing import Any

import httpx

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE,
    DEFAULT_PHOTOS_LIMIT,
    PATH_CAMERA_ALL,
    PATH_CAMERA_COMMAND,
    PATH_CAMERA_SETTINGS,
    PATH_LOGIN,
    PATH_PHOTO_ALL,
    PATH_PHOTO_VIDEO,
)

_LOGGER = logging.getLogger(__name__)


def photo_media_url(photo: dict[str, Any], *, size: str = "large") -> str | None:
    """Build a photo URL from a media descriptor."""
    media = photo.get(size)
    if not isinstance(media, dict):
        return None
    host = media.get("host")
    path = media.get("path")
    if not host or not path:
        return None
    return f"https://{host}/{path}"


def parse_spypoint_timestamp(value: str | None) -> datetime | None:
    """Parse a Spypoint timestamp sent as local time with a UTC suffix."""
    if value is None:
        return None
    if not (parsed := dt_util.parse_datetime(str(value))):
        return None
    # Drop the incorrect UTC offset and treat the components as local time.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return dt_util.as_local(parsed)


_DMS_COORDINATE = re.compile(r"^([NSEW])\s*(\d+)\s+([\d.]+)$")


def _parse_dms_coordinate(value: str) -> float | None:
    """Parse a Spypoint DMS coordinate string."""
    if not (match := _DMS_COORDINATE.match(value.strip())):
        return None
    direction, degrees, minutes = match.groups()
    decimal = float(degrees) + float(minutes) / 60
    if direction in ("S", "W"):
        decimal = -decimal
    return decimal


def get_camera_coordinates(camera: dict[str, Any]) -> tuple[float, float] | None:
    """Return latitude and longitude for a camera."""
    status = camera.get("status") or {}
    coordinates_list = status.get("coordinates")
    if not isinstance(coordinates_list, list) or not coordinates_list:
        return None

    coordinate = coordinates_list[-1]
    if not isinstance(coordinate, dict):
        return None

    position = coordinate.get("position") or {}
    position_coordinates = position.get("coordinates")
    if isinstance(position_coordinates, list) and len(position_coordinates) >= 2:
        longitude, latitude = position_coordinates[0], position_coordinates[1]
        return float(latitude), float(longitude)

    latitude_value = coordinate.get("latitude")
    longitude_value = coordinate.get("longitude")
    if latitude_value is None or longitude_value is None:
        return None

    latitude = _parse_dms_coordinate(str(latitude_value))
    longitude = _parse_dms_coordinate(str(longitude_value))
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


class SpypointAuthError(HomeAssistantError):
    """Error to indicate invalid authentication."""


class SpypointConnectionError(HomeAssistantError):
    """Error to indicate a connection problem."""


class SpypointDeviceResponse:
    """API response wrapper."""

    def __init__(self, resp: httpx.Response) -> None:
        """Initialize from an HTTP response."""
        _LOGGER.debug(
            "<- %d %s: %s",
            resp.status_code,
            resp.reason_phrase,
            re.sub(r"\s+", " ", resp.text),
        )
        self._status_code = resp.status_code
        self._result: Any = None

        if not self.is_success:
            return

        if not resp.text:
            self._result = {}
            return

        try:
            self._result = resp.json()
        except JSONDecodeError:
            _LOGGER.debug("Non-JSON success response from Spypoint API")
            self._result = {}

    @property
    def is_success(self) -> bool:
        """Return true for successful HTTP responses."""
        return 200 <= self._status_code < 300

    @property
    def status_code(self) -> int:
        """Return the HTTP status code."""
        return self._status_code

    @property
    def result(self) -> Any:
        """Return the parsed response body."""
        return self._result

    @property
    def has_result(self) -> bool:
        """Return true if the response body is valid."""
        return self.is_success and self._result is not None


class SpypointDevice:
    """Spypoint cloud API interface."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
    ) -> None:
        """Set up the device client."""
        self._hass = hass
        self._username = username
        self._password = password
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._user_id: str | None = None
        self._account_title: str | None = None
        self._response: SpypointDeviceResponse | None = None

    def _create_client(self) -> httpx.AsyncClient:
        """Create the HTTP client."""
        return httpx.AsyncClient(
            base_url=API_BASE,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    async def async_setup(self) -> None:
        """Create the HTTP client without blocking the event loop."""
        await self._ensure_client()

    async def async_close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the HTTP client, creating it on first use."""
        if self._client is None:
            self._client = await self._hass.async_add_executor_job(self._create_client)
        return self._client

    @property
    def user_id(self) -> str | None:
        """Return the authenticated user id."""
        return self._user_id

    @property
    def account_title(self) -> str | None:
        """Return a display name for the account."""
        return self._account_title

    @property
    def online(self) -> bool:
        """Return true when authenticated."""
        return self._token is not None

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers."""
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | list[Any] | None = None,
        auth: bool = True,
    ) -> bool:
        """Make an API request."""
        headers = self._auth_headers() if auth else {}
        tries = 2

        while tries > 0:
            try:
                _LOGGER.debug("-> %s %s", method, path)
                client = await self._ensure_client()
                response = await client.request(
                    method, path, json=json, headers=headers
                )
                self._response = SpypointDeviceResponse(response)

                if (
                    auth
                    and self._response.status_code == httpx.codes.UNAUTHORIZED
                    and path != PATH_LOGIN
                ):
                    await self.login(force=True)
                    headers = self._auth_headers()
                    tries -= 1
                    continue

                return self._response.is_success

            except httpx.HTTPError as exc:
                _LOGGER.error("Request error (%s %s): %s", method, path, exc)
                tries -= 1

        return False

    async def login(self, *, force: bool = False) -> bool:
        """Authenticate with the Spypoint cloud."""
        if not force and self._token:
            return True

        if not await self._request(
            "POST",
            PATH_LOGIN,
            json={"username": self._username, "password": self._password},
            auth=False,
        ):
            if self._response and self._response.status_code == httpx.codes.UNAUTHORIZED:
                raise SpypointAuthError
            raise SpypointConnectionError

        assert self._response is not None
        body = self._response.result
        self._token = body.get("token")
        self._user_id = body.get("uuid") or body.get("id")
        self._account_title = body.get("email") or self._username
        return self._token is not None and self._user_id is not None

    async def get_cameras(self) -> list[dict[str, Any]]:
        """Return all cameras on the account."""
        await self.login()
        if not await self._request("GET", PATH_CAMERA_ALL):
            if self._response and self._response.status_code == httpx.codes.UNAUTHORIZED:
                raise SpypointAuthError
            raise SpypointConnectionError

        assert self._response is not None
        cameras = self._response.result
        if not isinstance(cameras, list):
            raise SpypointConnectionError
        return cameras

    async def get_photos(
        self,
        camera_ids: list[str],
        *,
        limit: int | None = None,
        date_start: datetime | None = None,
        date_end: datetime | None = None,
        media_types: list[str] | None = None,
        species: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent photos for the given cameras."""
        await self.login()

        if date_end is None:
            date_end = datetime.now(tz=UTC)
        if date_start is None:
            date_start = date_end - timedelta(days=7)

        payload = {
            "camera": camera_ids,
            "dateStart": date_start.strftime("%Y-%m-%d %H:%M:%S"),
            "dateEnd": date_end.strftime("%Y-%m-%d %H:%M:%S"),
            "limit": limit or DEFAULT_PHOTOS_LIMIT,
            "mediaTypes": media_types or [],
            "species": species or [],
            "timeOfDay": [],
            "customTags": [],
        }

        if not await self._request("POST", PATH_PHOTO_ALL, json=payload):
            if self._response and self._response.status_code == httpx.codes.UNAUTHORIZED:
                raise SpypointAuthError
            raise SpypointConnectionError

        assert self._response is not None
        photos = self._response.result.get("photos", [])
        if not isinstance(photos, list):
            return []
        return photos

    async def get_latest_photos(
        self, camera_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        """Return the latest photo for each camera."""

        async def _fetch(camera_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                photos = await self.get_photos([camera_id], limit=1)
            except SpypointConnectionError:
                _LOGGER.debug(
                    "Unable to fetch latest photo for camera %s", camera_id
                )
                return camera_id, None
            return camera_id, photos[0] if photos else None

        if not camera_ids:
            return {}

        results = await asyncio.gather(*(_fetch(camera_id) for camera_id in camera_ids))
        return dict(results)

    async def async_request_hd_video(
        self, camera_id: str, photo_id: str
    ) -> dict[str, Any]:
        """Request an HD version of a photo/video."""
        await self.login()
        path = f"{PATH_PHOTO_VIDEO}/{camera_id}/{photo_id}"
        if not await self._request("POST", path, json={"quality": "hd"}):
            if self._response and self._response.status_code == httpx.codes.UNAUTHORIZED:
                raise SpypointAuthError
            raise SpypointConnectionError

        assert self._response is not None
        if isinstance(self._response.result, dict):
            return self._response.result
        return {}

    async def get_camera_settings(self, camera_id: str) -> dict[str, Any]:
        """Return settings for a camera."""
        await self.login()
        if not await self._request("GET", f"{PATH_CAMERA_SETTINGS}/{camera_id}"):
            raise SpypointConnectionError
        assert self._response is not None
        return self._response.result

    async def update_camera_settings(
        self, camera_id: str, settings: dict[str, Any]
    ) -> bool:
        """Update camera settings."""
        current = await self.get_camera_settings(camera_id)
        current.update(settings)
        return await self._request(
            "PUT", f"{PATH_CAMERA_SETTINGS}/{camera_id}", json=current
        )

    async def async_send_command(self, camera_id: str, command: str) -> bool:
        """Send an on-demand camera command."""
        await self.login()
        payload = {"cameraId": camera_id, "command": command}
        if await self._request("POST", PATH_CAMERA_COMMAND, json=payload):
            return True

        settings = {
            "operationMode": "instant",
            "captureMode": "photo" if command == "takePhoto" else "video",
            "capture": True,
        }
        return await self.update_camera_settings(camera_id, settings)

    async def async_take_photo(self, camera_id: str) -> bool:
        """Request an on-demand photo."""
        return await self.async_send_command(camera_id, "takePhoto")

    async def async_take_video(self, camera_id: str) -> bool:
        """Request an on-demand video."""
        return await self.async_send_command(camera_id, "takeVideo")

    async def async_request_capture_at_sync(self, camera_id: str) -> bool:
        """Request a photo at the next scheduled sync."""
        return await self.update_camera_settings(camera_id, {"capture": True})
