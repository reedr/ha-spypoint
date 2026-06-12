"""Spypoint coordinator."""

from __future__ import annotations

from datetime import timedelta
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
    DOMAIN,
    EVENT_PHOTO_COUNT_CHANGED,
    get_update_interval_seconds,
    next_aligned_update_time,
    should_align_update_interval,
)
from .device import SpypointAuthError, SpypointConnectionError, SpypointDevice
from .entity import camera_name, last_update, photo_count, photo_limit

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
            update_interval=timedelta(
                seconds=get_update_interval_seconds(config_entry)
            ),
            always_update=False,
        )
        self._device = device
        self._photo_counts: dict[str, int | None] = {}

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

    async def _async_setup(self) -> None:
        """Perform one-time setup before the first refresh."""
        await self.device.login()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch camera and latest photo data from the Spypoint API."""
        try:
            cameras_list = await self.device.get_cameras()
        except SpypointAuthError as err:
            raise ConfigEntryAuthFailed from err
        except SpypointConnectionError as err:
            raise UpdateFailed("Unable to reach Spypoint API") from err

        cameras = {camera["id"]: camera for camera in cameras_list if camera.get("id")}
        latest_photos: dict[str, dict[str, Any] | None] = {}

        if cameras:
            try:
                latest_photos = await self.device.get_latest_photos(list(cameras))
            except SpypointAuthError as err:
                raise ConfigEntryAuthFailed from err

        self._async_fire_photo_count_events(cameras)

        return {"cameras": cameras, "latest_photos": latest_photos}

    @callback
    def _async_fire_photo_count_events(
        self, cameras: dict[str, dict[str, Any]]
    ) -> None:
        """Fire events when a camera photo count changes."""
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        for camera_id, camera in cameras.items():
            new_count = photo_count(camera)
            previous_count = self._photo_counts.get(camera_id)
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

            self.hass.bus.async_fire(
                EVENT_PHOTO_COUNT_CHANGED,
                {
                    "entity_id": count_entity_id,
                    "camera_name": camera_name(camera),
                    "device_id": device.id if device else None,
                    "photo_count": new_count,
                    "photo_limit": photo_limit(camera),
                    "last_update": (
                        last_update_time.isoformat() if last_update_time else None
                    ),
                },
            )
            _LOGGER.debug(
                "Photo count changed for %s: %s -> %s",
                camera_name(camera),
                previous_count,
                new_count,
            )

    @callback
    def _schedule_refresh(self) -> None:
        """Schedule a refresh, aligning to local :05 when interval is 30m-based."""
        if self._update_interval_seconds is None:
            return

        if self.config_entry and self.config_entry.pref_disable_polling:
            return

        self._async_unsub_refresh()

        update_interval = self._update_interval_seconds
        if self._retry_after is not None:
            update_interval = self._retry_after
            self._retry_after = None

        if should_align_update_interval(int(update_interval)):
            now = dt_util.now()
            next_refresh_time = next_aligned_update_time(now, int(update_interval))
            delay = (next_refresh_time - now).total_seconds() + self._microsecond
            _LOGGER.debug(
                "Scheduling aligned refresh at %s (in %.1f seconds)",
                next_refresh_time,
                delay,
            )
        else:
            delay = update_interval + self._microsecond

        next_refresh = int(self.hass.loop.time()) + max(0, delay)
        self._unsub_refresh = self.hass.loop.call_at(
            next_refresh, self.__wrap_handle_refresh_interval
        ).cancel
