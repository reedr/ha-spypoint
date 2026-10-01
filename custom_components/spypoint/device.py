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
    PATH_CAMERA,
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


def get_camera_coordinate_attributes(
    camera: dict[str, Any],
) -> dict[str, str] | None:
    """Return coordinate metadata for the latest camera position."""
    coordinates_list = (camera.get("status") or {}).get("coordinates") or []
    if not coordinates_list:
        return None
    coordinate = coordinates_list[-1]
    if not isinstance(coordinate, dict):
        return None
    attributes: dict[str, str] = {}
    if geohash := coordinate.get("geohash"):
        attributes["geohash"] = str(geohash)
    if date_time := coordinate.get("dateTime"):
        if parsed := parse_spypoint_timestamp(str(date_time)):
            attributes["date_time"] = parsed.isoformat()
        else:
            attributes["date_time"] = str(date_time)
    return attributes or None


class SpypointAuthError(HomeAssistantError):
    """Error to indicate invalid authentication."""


class SpypointConnectionError(HomeAssistantError):
    """Error to indicate a connection problem."""


class SpypointRequestError(HomeAssistantError):
    """Spypoint rejected a request; the message is Spypoint's own explanation."""


class SpypointDeviceResponse:
    """API response wrapper."""

    def __init__(self, resp: httpx.Response) -> None:
        """Initialize from an HTTP response."""
        # Login replies carry the bearer token and photo lists carry signed
        # URLs; log only the status for those.
        path = resp.request.url.path if resp.request else ""
        quiet = path.endswith(PATH_LOGIN) or path.endswith(PATH_PHOTO_ALL)
        _LOGGER.debug(
            "<- %d %s: %s",
            resp.status_code,
            resp.reason_phrase,
            "(body not logged)" if quiet else re.sub(r"\s+", " ", resp.text),
        )
        self._status_code = resp.status_code
        self._result: Any = None
        self._error_message: str | None = None

        if not self.is_success:
            self._error_message = _error_message(resp)
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

    @property
    def error_message(self) -> str | None:
        """Return Spypoint's explanation of a failed request, if it gave one."""
        return self._error_message


def _error_message(resp: httpx.Response) -> str:
    """Extract a readable reason from a failed response.

    Spypoint answers a refused request with a JSON body such as
    {"error": "User has run out of videos", "message": "User has run out of
    videos"}; prefer "message", then "error", then the plain-text body, then
    the HTTP reason phrase.
    """
    try:
        body = resp.json()
    except (JSONDecodeError, ValueError):
        body = None
    if isinstance(body, dict):
        for key in ("message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    text = resp.text.strip()
    if text and len(text) <= 200 and not text.startswith("<"):
        return text
    return f"HTTP {resp.status_code} {resp.reason_phrase}".strip()


# Concurrent requests to the cloud API; per-camera fetches are gathered.
MAX_CONCURRENT_REQUESTS = 5


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
        self._login_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

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
    ) -> SpypointDeviceResponse | None:
        """Make an API request; return its response, or None if none arrived.

        A 401 renews the token once and retries. Each call gets its own
        response, so concurrent requests can't read each other's.
        """
        response: SpypointDeviceResponse | None = None
        tries = 2
        while tries > 0:
            token = self._token
            headers = self._auth_headers() if auth else {}
            try:
                _LOGGER.debug("-> %s %s", method, path)
                client = await self._ensure_client()
                async with self._semaphore:
                    resp = await client.request(method, path, json=json, headers=headers)
                response = SpypointDeviceResponse(resp)
            except httpx.HTTPError as exc:
                _LOGGER.error("Request error (%s %s): %s", method, path, exc)
                tries -= 1
                continue

            if auth and response.status_code == httpx.codes.UNAUTHORIZED and path != PATH_LOGIN:
                await self._relogin(token)
                tries -= 1
                continue
            return response
        return response

    async def _relogin(self, stale_token: str | None) -> None:
        """Renew the token once, however many requests saw it expire."""
        async with self._login_lock:
            if self._token != stale_token:
                return  # another request already renewed it
            await self._do_login()

    async def _do_login(self) -> bool:
        response = await self._request(
            "POST",
            PATH_LOGIN,
            json={"username": self._username, "password": self._password},
            auth=False,
        )
        if response is None or not response.is_success:
            if response is not None and response.status_code == httpx.codes.UNAUTHORIZED:
                raise SpypointAuthError
            raise SpypointConnectionError
        body = response.result if isinstance(response.result, dict) else {}
        self._token = body.get("token")
        self._user_id = body.get("uuid") or body.get("id")
        self._account_title = body.get("email") or self._username
        return self._token is not None and self._user_id is not None

    async def login(self, *, force: bool = False) -> bool:
        """Authenticate with the Spypoint cloud."""
        if not force and self._token:
            return True
        async with self._login_lock:
            if not force and self._token:
                return True
            return await self._do_login()

    async def _call(
        self, method: str, path: str, *, json: dict[str, Any] | list[Any] | None = None
    ) -> Any:
        """Make an authenticated request and return its body, or raise."""
        await self.login()
        response = await self._request(method, path, json=json)
        if response is None:
            raise SpypointConnectionError
        if not response.is_success:
            if response.status_code == httpx.codes.UNAUTHORIZED:
                raise SpypointAuthError
            if response.error_message and 400 <= response.status_code < 500:
                raise SpypointRequestError(response.error_message)
            raise SpypointConnectionError
        return response.result

    async def get_cameras(self) -> list[dict[str, Any]]:
        """Return all cameras on the account."""
        cameras = await self._call("GET", PATH_CAMERA_ALL)
        if not isinstance(cameras, list):
            raise SpypointConnectionError
        return [camera for camera in cameras if isinstance(camera, dict)]

    async def get_camera(self, camera_id: str) -> dict[str, Any]:
        """Return a single camera by id."""
        try:
            camera = await self._call("GET", f"{PATH_CAMERA}/{camera_id}")
        except SpypointRequestError as err:
            raise SpypointConnectionError from err
        if not isinstance(camera, dict):
            raise SpypointConnectionError
        return camera

    async def get_cameras_by_id(
        self, camera_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        """Return camera data for the given ids."""

        async def _fetch(camera_id: str) -> tuple[str, dict[str, Any] | None]:
            try:
                return camera_id, await self.get_camera(camera_id)
            except SpypointAuthError:
                raise
            except SpypointConnectionError:
                _LOGGER.debug("Unable to fetch camera %s", camera_id)
                return camera_id, None

        if not camera_ids:
            return {}

        results = await asyncio.gather(*(_fetch(camera_id) for camera_id in camera_ids))
        return dict(results)

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

        try:
            body = await self._call("POST", PATH_PHOTO_ALL, json=payload)
        except SpypointRequestError as err:
            raise SpypointConnectionError from err
        photos = body.get("photos", []) if isinstance(body, dict) else []
        if not isinstance(photos, list):
            return []
        return [photo for photo in photos if isinstance(photo, dict)]

    async def get_latest_photos(
        self, camera_ids: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        """Return the latest photo for each camera whose fetch succeeded.

        A camera whose fetch failed is left out (not mapped to None), so the
        caller keeps the photo it already has and can retry later.
        """

        async def _fetch(camera_id: str) -> tuple[str, dict[str, Any] | None] | None:
            try:
                photos = await self.get_photos([camera_id], limit=1)
            except SpypointConnectionError:
                _LOGGER.debug("Unable to fetch latest photo for camera %s", camera_id)
                return None
            return camera_id, photos[0] if photos else None

        if not camera_ids:
            return {}

        results = await asyncio.gather(*(_fetch(camera_id) for camera_id in camera_ids))
        return dict(result for result in results if result is not None)

    async def async_request_hd_video(
        self, camera_id: str, photo_id: str
    ) -> dict[str, Any]:
        """Request an HD version of a photo/video.

        Raises SpypointRequestError with Spypoint's explanation when it refuses
        (e.g. "User has run out of videos").
        """
        path = f"{PATH_PHOTO_VIDEO}/{camera_id}/{photo_id}"
        result = await self._call("POST", path, json={"quality": "hd"})
        return result if isinstance(result, dict) else {}

    async def get_camera_settings(self, camera_id: str) -> dict[str, Any]:
        """Return settings for a camera."""
        try:
            settings = await self._call("GET", f"{PATH_CAMERA_SETTINGS}/{camera_id}")
        except SpypointRequestError as err:
            raise SpypointConnectionError from err
        if not isinstance(settings, dict):
            raise SpypointConnectionError
        return settings

    async def update_camera_settings(
        self, camera_id: str, settings: dict[str, Any]
    ) -> bool:
        """Update camera settings."""
        current = await self.get_camera_settings(camera_id)
        current.update(settings)
        response = await self._request(
            "PUT", f"{PATH_CAMERA_SETTINGS}/{camera_id}", json=current
        )
        return response is not None and response.is_success

    async def async_send_command(self, camera_id: str, command: str) -> bool:
        """Send an on-demand camera command.

        Only when Spypoint answers that the command endpoint doesn't apply to
        this camera (400/404) does it fall back to switching the camera to
        instant mode with a capture request. A network error or server error
        never changes the camera's settings.
        """
        await self.login()
        payload = {"cameraId": camera_id, "command": command}
        response = await self._request("POST", PATH_CAMERA_COMMAND, json=payload)
        if response is not None and response.is_success:
            return True
        if response is None or response.status_code not in (
            httpx.codes.BAD_REQUEST,
            httpx.codes.NOT_FOUND,
        ):
            return False

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
