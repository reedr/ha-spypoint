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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    COORDINATOR_UPDATE_INTERVAL_SECONDS,
    DOMAIN,
    EVENT_NEW_PHOTOS,
    POSITION_LOCK_STORAGE_VERSION,
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
    camera_status_refresh_interval_seconds,
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
        self._last_camera_refresh: dict[str, datetime] = {}
        self._last_discovery_refresh: datetime | None = None
        self._force_full_refresh = False
        self._position_locked: set[str] = set()
        self._locked_coordinates: dict[str, tuple[float, float]] = {}
        self._locked_coordinate_attributes: dict[str, dict[str, str] | None] = {}
        self._position_lock_store = Store[dict[str, Any]](
            hass,
            POSITION_LOCK_STORAGE_VERSION,
            f"{DOMAIN}_{config_entry.entry_id}_position_locks",
        )

    @property
    def device(self) -> SpypointDevice:
        """Return the API device handle."""
        return self._device

    @property
    def camera_ids(self) -> set[str]:
        """Return the ids of the cameras currently known to the API."""
        if not self.data:
            return set()
        return set(self.data.get("cameras", {}))

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

    @property
    def position_lock_state(self) -> dict[str, dict[str, Any]]:
        """Return the persisted GPS lock state, keyed by camera id."""
        camera_ids = (
            self._position_locked
            | set(self._locked_coordinates)
            | set(self._locked_coordinate_attributes)
        )
        state: dict[str, dict[str, Any]] = {}
        for camera_id in camera_ids:
            entry: dict[str, Any] = {"locked": camera_id in self._position_locked}
            if (coords := self._locked_coordinates.get(camera_id)) is not None:
                entry["latitude"] = coords[0]
                entry["longitude"] = coords[1]
            if (attrs := self._locked_coordinate_attributes.get(camera_id)) is not None:
                entry["attributes"] = attrs
            state[camera_id] = entry
        return state

    @callback
    def set_position_locked(self, camera_id: str, locked: bool) -> None:
        """Lock or unlock device tracker updates for a camera.

        Coordinates are captured only on an unlocked -> locked transition. A
        camera that is already locked keeps the position it was frozen at:
        re-capturing here would silently re-baseline the lock onto whatever
        the API reports at that moment. Toggle the switch off and on to
        deliberately re-freeze at the current position.
        """
        if locked:
            if camera_id in self._position_locked:
                return
            self._capture_locked_position(camera_id)
            self._position_locked.add(camera_id)
        else:
            self._position_locked.discard(camera_id)
            self._locked_coordinates.pop(camera_id, None)
            self._locked_coordinate_attributes.pop(camera_id, None)
        self.async_update_listeners()
        self.hass.async_create_task(self._async_save_position_locks())

    @callback
    def restore_position_locked(self, camera_id: str) -> None:
        """Re-apply a lock from a restored switch state.

        Unlike set_position_locked this never captures coordinates: the frozen
        position comes from the position lock store, or from the device
        tracker's own restored state via seed_locked_coordinates.
        """
        if camera_id in self._position_locked:
            return
        self._position_locked.add(camera_id)
        self.async_update_listeners()
        self.hass.async_create_task(self._async_save_position_locks())

    @callback
    def seed_locked_coordinates(
        self,
        camera_id: str,
        coordinates: tuple[float, float],
        attributes: dict[str, str] | None = None,
    ) -> None:
        """Adopt a restored position for a lock that has no frozen coordinates.

        Covers locks stored before coordinates were persisted. Never overwrites
        coordinates that are already frozen.
        """
        if camera_id not in self._position_locked:
            return
        if camera_id in self._locked_coordinates:
            return
        _LOGGER.debug(
            "Seeding frozen position for locked camera %s from restored state",
            camera_id,
        )
        self._locked_coordinates[camera_id] = coordinates
        if attributes:
            self._locked_coordinate_attributes[camera_id] = attributes
        self.hass.async_create_task(self._async_save_position_locks())

    def _capture_locked_position(self, camera_id: str) -> None:
        """Freeze the camera's current coordinates at the moment of locking."""
        if (camera := self.get_camera(camera_id)) is None:
            return
        if (coords := get_camera_coordinates(camera)) is not None:
            self._locked_coordinates[camera_id] = coords
        if (attrs := get_camera_coordinate_attributes(camera)) is not None:
            self._locked_coordinate_attributes[camera_id] = attrs
        _LOGGER.debug(
            "Freezing position for camera %s at %s",
            camera_id,
            self._locked_coordinates.get(camera_id),
        )

    async def _async_load_position_locks(self) -> None:
        """Restore persisted GPS lock state."""
        stored = await self._position_lock_store.async_load() or {}
        for camera_id, lock_data in stored.get("locks", {}).items():
            self._position_locked.add(camera_id)
            latitude = lock_data.get("latitude")
            longitude = lock_data.get("longitude")
            if latitude is not None and longitude is not None:
                self._locked_coordinates[camera_id] = (latitude, longitude)
            attributes = lock_data.get("attributes")
            if isinstance(attributes, dict):
                self._locked_coordinate_attributes[camera_id] = attributes

    async def _async_save_position_locks(self) -> None:
        """Persist GPS lock state."""
        locks: dict[str, dict[str, Any]] = {}
        for camera_id in self._position_locked:
            lock_data: dict[str, Any] = {}
            if (coords := self._locked_coordinates.get(camera_id)) is not None:
                lock_data["latitude"] = coords[0]
                lock_data["longitude"] = coords[1]
            if (attrs := self._locked_coordinate_attributes.get(camera_id)) is not None:
                lock_data["attributes"] = attrs
            locks[camera_id] = lock_data
        await self._position_lock_store.async_save({"locks": locks})

    async def _async_setup(self) -> None:
        """Perform one-time setup before the first refresh."""
        await self._async_load_position_locks()
        await self.device.async_setup()
        await self.device.login()

    async def async_refresh_cameras(self) -> None:
        """Force an immediate full camera data refresh."""
        self._force_full_refresh = True
        await self.async_request_refresh()

    async def async_refresh_camera_list(self) -> None:
        """Fetch the full camera list now, bypassing the request debouncer."""
        self._force_full_refresh = True
        await self.async_refresh()

    def _should_discovery_refresh(self, now: datetime) -> bool:
        """Return True when the full camera list should be fetched."""
        interval_seconds = get_update_interval_seconds(self.config_entry)
        return should_aligned_refresh(
            self._last_discovery_refresh, now, interval_seconds
        )

    def _camera_due_for_refresh(
        self, camera_id: str, camera: dict[str, Any], now: datetime
    ) -> bool:
        """Return True when a camera should fetch updated status."""
        interval_seconds = camera_status_refresh_interval_seconds(
            camera, self.config_entry
        )
        return should_aligned_refresh(
            self._last_camera_refresh.get(camera_id), now, interval_seconds
        )

    async def _async_full_refresh(
        self, now: datetime
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Fetch all cameras and reset per-camera refresh timestamps."""
        _LOGGER.debug("Performing full camera refresh")
        cameras_list = await self.device.get_cameras()
        cameras = {
            camera["id"]: camera for camera in cameras_list if camera.get("id")
        }
        self._last_camera_refresh = {
            camera_id: now for camera_id in cameras
        }
        self._last_discovery_refresh = now
        self._force_full_refresh = False
        self._prune_removed_cameras(set(cameras))
        return cameras, set(cameras)

    def registered_camera_ids(self) -> set[str]:
        """Return camera ids that still have a device in the registry."""
        device_registry = dr.async_get(self.hass)
        devices = dr.async_entries_for_config_entry(
            device_registry, self.config_entry.entry_id
        )
        return {
            identifier
            for device in devices
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        }

    def _prune_removed_cameras(self, current_ids: set[str]) -> None:
        """Drop cached state for cameras the API no longer returns.

        Only runs on a full refresh, where the fetched list is authoritative.
        An empty list is treated as an API glitch rather than an account with
        no cameras, so nothing is discarded in that case.

        GPS locks hold coordinates the user pinned by hand, so they are only
        discarded once the camera's device has also been removed from the
        registry. A camera that vanishes from the API transiently keeps its
        frozen position until the device itself is deleted.
        """
        if not current_ids:
            return

        for stale_id in set(self._photo_counts) - current_ids:
            self._photo_counts.pop(stale_id, None)
        for stale_id in set(self._new_capture_counts) - current_ids:
            self._new_capture_counts.pop(stale_id, None)

        live_ids = current_ids | self.registered_camera_ids()
        stale_locks = self._position_locked - live_ids
        stale_coords = set(self._locked_coordinates) - live_ids
        stale_attrs = set(self._locked_coordinate_attributes) - live_ids
        if not (stale_locks or stale_coords or stale_attrs):
            return

        _LOGGER.debug(
            "Pruning GPS position lock state for removed camera(s): %s",
            ", ".join(sorted(stale_locks | stale_coords | stale_attrs)),
        )
        self._position_locked -= stale_locks
        for stale_id in stale_coords:
            self._locked_coordinates.pop(stale_id, None)
        for stale_id in stale_attrs:
            self._locked_coordinate_attributes.pop(stale_id, None)
        self.hass.async_create_task(self._async_save_position_locks())

    async def _async_fetch_cameras(
        self,
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Fetch camera data using full or per-camera refresh."""
        now = dt_util.now()
        previous_cameras = (self.data or {}).get("cameras", {}) if self.data else {}

        if self._force_full_refresh or not previous_cameras:
            return await self._async_full_refresh(now)

        if self._should_discovery_refresh(now):
            return await self._async_full_refresh(now)

        cameras = dict(previous_cameras)
        due_ids = [
            camera_id
            for camera_id, camera in cameras.items()
            if self._camera_due_for_refresh(camera_id, camera, now)
        ]

        if not due_ids:
            _LOGGER.debug("Skipping camera refresh; no cameras due")
            return cameras, set()

        _LOGGER.debug(
            "Refreshing %d camera(s) due for status update: %s",
            len(due_ids),
            ", ".join(due_ids),
        )
        fetched = await self.device.get_cameras_by_id(due_ids)
        fetched_ids: set[str] = set()
        for camera_id, camera in fetched.items():
            if camera is not None:
                cameras[camera_id] = camera
                fetched_ids.add(camera_id)
                self._last_camera_refresh[camera_id] = now

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

        previous_new_captures = dict((self.data or {}).get("new_captures", {}))
        self._async_process_photo_count_changes(cameras, fetched_ids)

        new_captures = {
            camera_id: count
            for camera_id, count in previous_new_captures.items()
            if camera_id in cameras
        }
        for camera_id in fetched_ids:
            new_captures[camera_id] = self._new_capture_counts.get(camera_id, 0)
        self._new_capture_counts = new_captures

        return {
            "cameras": cameras,
            "latest_photos": latest_photos,
            "new_captures": new_captures,
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
