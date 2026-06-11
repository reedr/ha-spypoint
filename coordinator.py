"""Spypoint coordinator."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_UPDATE_INTERVAL_SECONDS
from .device import SpypointAuthError, SpypointConnectionError, SpypointDevice

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
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL_SECONDS),
            always_update=False,
        )
        self._device = device

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

        return {"cameras": cameras, "latest_photos": latest_photos}
