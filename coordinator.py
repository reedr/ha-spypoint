"""Spypoint coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    COORDINATOR_UPDATE_INTERVAL_SECONDS,
    DOMAIN,
    EVENT_NEW_PHOTOS,
    get_instant_update_interval_seconds,
    get_update_interval_seconds,
    should_aligned_refresh,
)
from .device import (
    SpypointAuthError,
    SpypointConnectionError,
    SpypointDevice,
    get_camera_coordinate_attributes,
    get_camera_coordinates,
)
from .camera_data import (
    camera_name,
    is_instant_mode,
    last_update,
    new_photo_count_for_event,
    photo_count,
    photo_limit,
)

_LOGGER = logging.getLogger(__name__)

type SpypointConfigEntry = ConfigEntry[SpypointCoordinator]


class SpypointCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Spypoint camera data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SpypointConfigEntry,
        device: SpypointDevice,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Spypoint Coordinator",
            config_entry=config_entry,
            update_interval=timedelta(seconds=COORDINATOR_UPDATE_INTERVAL_SECONDS),
            always_update=False,
        )
        self._device = device
        self._photo_counts: dict[str, int | None] = {}
        self._new_capture_counts: dict[str, int] = {}
        self._last_full_refresh: datetime | None = None
        self._last_instant_refresh: datetime | None = None
        self._force_full_refresh = False
        self._position_locked: set[str] = set()
        self._locked_coordinates: dict[str, tuple[float, float]] = {}
        self._locked_coordinate_attributes: dict[str, dict[str, str] | None] = {}

    @property
    def device(self) -> SpypointDevice:
        """Return the API device handle."""
        return self._device

    def get_camera(self, camera_id: str) -> dict[str, Any] | None:
        """Return a camera by id."""
        if not self.data:
            return None
        return self.data.get("cameras", {}).get(camera_id)

    def get_latest_photo(self, camera_id: str) -> dict[str, Any] | None:
        """Return the latest photo metadata for a camera."""
        if not self.data:
            return None
        return self.data.get("latest_photos", {}).get(camera_id)

    def get_new_capture_count(self, camera_id: str) -> int:
        """Return the adjusted new capture count from the last update."""
        if not self.data:
            return 0
        return self.data.get("new_captures", {}).get(camera_id, 0)

    def is_position_locked(self, camera_id: str) -> bool:
        """Return True when the camera location should ignore API updates."""
        return camera_id in self._position_locked

    def get_locked_coordinates(self, camera_id: str) -> tuple[float, float] | None:
        """Return frozen coordinates for a position-locked camera."""
        return self._locked_coordinates.get(camera_id)

    def get_locked_coordinate_attributes(
        self, camera_id: str
    ) -> dict[str, str] | None:
        """Return frozen coordinate metadata for a position-locked camera."""
        return self._locked_coordinate_attributes.get(camera_id)

    @callback
    def set_position_locked(self, camera_id: str, locked: bool) -> None:
        """Lock or unlock device tracker updates for a camera."""
        if locked:
            camera = self.get_camera(camera_id)
        if camera is not None:
            coords = get_camera_coordinates(camera)
            if coords is not None:
                self._locked_coordinates[camera_id] = coords
            self._locked_coordinate_attributes[camera_id] = (
                get_camera_coordinate_attributes(camera)
            )
            self._position_locked.add(camera_id)
        else:
            self._position_locked.discard(camera_id)
            self._locked_coordinates.pop(camera_id, None)
            self._locked_coordinate_attributes.pop(camera_id, None)
        self.async_update_listeners()

    async def _async_setup(self) -> None:
        """Perform one-time setup before the first refresh."""
        await self.device.async_setup()
        await self.device.login()

    async def async_refresh_cameras(self) -> None:
        """Force an immediate full camera data refresh."""
        self._force_full_refresh = True
        await self.async_request_refresh()

    def _should_full_refresh(self, now: datetime) -> bool:
        """Return True when all cameras should be fetched."""
        if self._force_full_refresh:
            return True

        interval_seconds = get_update_interval_seconds(self.config_entry)
        return should_aligned_refresh(
            self._last_full_refresh, now, interval_seconds
        )

    def _should_instant_refresh(self, now: datetime) -> bool:
        """Return True when instant-mode cameras should be fetched."""
        interval_seconds = get_instant_update_interval_seconds(self.config_entry)
        return should_aligned_refresh(
            self._last_instant_refresh, now, interval_seconds
        )

    async def _async_fetch_cameras(
        self,
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Fetch camera data using full or incremental refresh."""
        now = dt_util.now()

        if self._should_full_refresh(now):
            _LOGGER.debug("Performing full camera refresh")
            cameras_list = await self.device.get_cameras()
            self._last_full_refresh = now
            self._last_instant_refresh = now
            self._force_full_refresh = False
            cameras = {
                camera["id"]: camera for camera in cameras_list if camera.get("id")
            }
            return cameras, set(cameras)

        if not self._should_instant_refresh(now):
            _LOGGER.debug("Skipping instant camera refresh; interval not elapsed")
            previous_cameras = (self.data or {}).get("cameras", {})
            return dict(previous_cameras), set()

        previous_cameras = (self.data or {}).get("cameras", {})
        cameras = dict(previous_cameras)
        instant_ids = [
            camera_id
            for camera_id, camera in cameras.items()
            if is_instant_mode(camera)
        ]

        if not instant_ids:
            _LOGGER.debug("Skipping camera refresh; no instant-mode cameras")
            self._last_instant_refresh = now
            return cameras, set()

        _LOGGER.debug(
            "Refreshing %d instant-mode camera(s): %s",
            len(instant_ids),
            ", ".join(instant_ids),
        )
        fetched = await self.device.get_cameras_by_id(instant_ids)
        fetched_ids: set[str] = set()
        for camera_id, camera in fetched.items():
            if camera is not None:
                cameras[camera_id] = camera
                fetched_ids.add(camera_id)

        self._last_instant_refresh = now
        return cameras, fetched_ids

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch camera and latest photo data from the Spypoint API."""
        try:
            cameras, fetched_ids = await self._async_fetch_cameras()
        except SpypointAuthError as err:
            raise ConfigEntryAuthFailed from err
        except SpypointConnectionError as err:
            raise UpdateFailed("Unable to reach Spypoint API") from err

        previous_latest = (self.data or {}).get("latest_photos", {})
        latest_photos = {
            camera_id: previous_latest[camera_id]
            for camera_id in cameras
            if camera_id in previous_latest
        }
        cameras_needing_photos = self._camera_ids_needing_latest_photo(
            cameras, fetched_ids
        )

        if cameras_needing_photos:
            try:
                fetched = await self.device.get_latest_photos(cameras_needing_photos)
            except SpypointAuthError as err:
                raise ConfigEntryAuthFailed from err
            latest_photos.update(fetched)

        self._async_process_photo_count_changes(cameras, fetched_ids)

        return {
            "cameras": cameras,
            "latest_photos": latest_photos,
            "new_captures": dict(self._new_capture_counts),
        }

    def _camera_ids_needing_latest_photo(
        self,
        cameras: dict[str, dict[str, Any]],
        fetched_ids: set[str],
    ) -> list[str]:
        """Return camera ids that should fetch a latest photo."""
        camera_ids: list[str] = []
        for camera_id in fetched_ids:
            camera = cameras.get(camera_id)
            if not camera:
                continue
            new_count = photo_count(camera)
            previous_count = self._photo_counts.get(camera_id)
            if previous_count is None:
                camera_ids.append(camera_id)
            elif new_count is not None and new_count != previous_count:
                camera_ids.append(camera_id)
        return camera_ids

    @callback
    def _async_process_photo_count_changes(
        self,
        cameras: dict[str, dict[str, Any]],
        fetched_ids: set[str],
    ) -> None:
        """Update new capture counts and fire an event when photos change."""
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        changed_cameras: list[dict[str, Any]] = []

        for camera_id, camera in cameras.items():
            if camera_id not in fetched_ids:
                continue
            new_count = photo_count(camera)
            previous_count = self._photo_counts.get(camera_id)

            if (
                previous_count is not None
                and new_count is not None
                and new_count != previous_count
            ):
                self._new_capture_counts[camera_id] = new_photo_count_for_event(
                    camera, new_count=new_count, previous_count=previous_count
                )
            else:
                self._new_capture_counts[camera_id] = 0

            self._photo_counts[camera_id] = new_count

            if (
                previous_count is None
                or new_count is None
                or new_count <= 0
                or new_count == previous_count
            ):
                continue

            device = device_registry.async_get_device(identifiers={(DOMAIN, camera_id)})
            count_entity_id = entity_registry.async_get_entity_id(
                SENSOR_DOMAIN, DOMAIN, f"{camera_id}_photo_count"
            )
            last_update_time = last_update(camera)
            new_photo_count = self._new_capture_counts[camera_id]

            changed_cameras.append(
                {
                    "entity_id": count_entity_id,
                    "camera_name": camera_name(camera),
                    "device_id": device.id if device else None,
                    "photo_count": new_count,
                    "new_photo_count": new_photo_count,
                    "photo_limit": photo_limit(camera),
                    "last_update": (
                        last_update_time.isoformat() if last_update_time else None
                    ),
                }
            )
            _LOGGER.debug(
                "New photos for %s: %s -> %s (%s new)",
                camera_name(camera),
                previous_count,
                new_count,
                new_photo_count,
            )

        if not changed_cameras:
            return

        self.hass.bus.async_fire(
            EVENT_NEW_PHOTOS,
            {"cameras": changed_cameras},
        )
