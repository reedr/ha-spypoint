"""Image platform for Spypoint Trail Cameras."""

from __future__ import annotations

import logging

from homeassistant.components.image import Image, ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .device import photo_media_url
from .entity import SpypointEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpypointConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Spypoint image entities."""
    coordinator = config_entry.runtime_data
    known_cameras: set[str] = set()

    def _check_cameras() -> None:
        if not coordinator.data:
            return
        current_cameras = set(coordinator.data.get("cameras", {}))
        new_cameras = current_cameras - known_cameras
        if not new_cameras:
            return
        known_cameras.update(new_cameras)
        async_add_entities(
            SpypointLatestPhoto(hass, coordinator, camera_id)
            for camera_id in new_cameras
        )

    _check_cameras()
    config_entry.async_on_unload(coordinator.async_add_listener(_check_cameras))


class SpypointLatestPhoto(SpypointEntity, ImageEntity):
    """Latest photo from a Spypoint trail camera."""

    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SpypointCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the image entity."""
        super().__init__(
            coordinator,
            camera_id,
            "latest_photo",
            entity_name="Latest photo",
        )
        ImageEntity.__init__(self, hass)
        self._attr_translation_key = "latest_photo"
        self._update_image_attrs()

    @callback
    def _update_image_attrs(self) -> None:
        """Update image URL and timestamp from coordinator data."""
        photo = self.coordinator.get_latest_photo(self.camera_id)
        if not photo:
            if self._attr_image_url is not None:
                self._cached_image = None
            self._attr_image_url = None
            self._attr_image_last_updated = None
            return

        image_url = photo_media_url(photo)
        if image_url != self._attr_image_url:
            _LOGGER.debug(
                "Updated latest photo URL for camera %s", self.camera_id
            )
            self._attr_image_url = image_url
            self._cached_image = None

        if date := photo.get("date"):
            self._attr_image_last_updated = dt_util.parse_datetime(str(date))

    async def _async_load_image_from_url(self, url: str) -> Image | None:
        """Load an image by URL, forcing JPEG content type."""
        if response := await self._fetch_url(url):
            return Image(content=response.content, content_type="image/jpeg")
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_image_attrs()
        super()._handle_coordinator_update()
