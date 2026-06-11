"""Button platform for Spypoint Trail Cameras."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .entity import SpypointEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpypointConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Spypoint buttons."""
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
        entities: list[ButtonEntity] = []
        for camera_id in new_cameras:
            camera = coordinator.get_camera(camera_id) or {}
            commands = camera.get("commands") or {}
            if "takePhoto" in commands:
                entities.append(SpypointTakePhotoButton(coordinator, camera_id))
            if "takeVideo" in commands:
                entities.append(SpypointTakeVideoButton(coordinator, camera_id))
            entities.append(SpypointCaptureAtSyncButton(coordinator, camera_id))
        if entities:
            async_add_entities(entities)

    _check_cameras()
    config_entry.async_on_unload(coordinator.async_add_listener(_check_cameras))


class SpypointButton(SpypointEntity, ButtonEntity):
    """Base Spypoint button."""

    async def async_press(self) -> None:
        """Handle the button press."""


class SpypointTakePhotoButton(SpypointButton):
    """Request an on-demand photo."""

    def __init__(self, coordinator: SpypointCoordinator, camera_id: str) -> None:
        """Initialize the button."""
        super().__init__(
            coordinator,
            camera_id,
            "take_photo",
            entity_name="Take photo",
        )
        self._attr_translation_key = "take_photo"

    async def async_press(self) -> None:
        """Request a photo from the camera."""
        await self.coordinator.device.async_take_photo(self.camera_id)


class SpypointTakeVideoButton(SpypointButton):
    """Request an on-demand video."""

    def __init__(self, coordinator: SpypointCoordinator, camera_id: str) -> None:
        """Initialize the button."""
        super().__init__(
            coordinator,
            camera_id,
            "take_video",
            entity_name="Take video",
        )
        self._attr_translation_key = "take_video"

    async def async_press(self) -> None:
        """Request a video from the camera."""
        await self.coordinator.device.async_take_video(self.camera_id)


class SpypointCaptureAtSyncButton(SpypointButton):
    """Request a capture at the next sync."""

    def __init__(self, coordinator: SpypointCoordinator, camera_id: str) -> None:
        """Initialize the button."""
        super().__init__(
            coordinator,
            camera_id,
            "capture_at_sync",
            entity_name="Capture at next sync",
        )
        self._attr_translation_key = "capture_at_sync"

    async def async_press(self) -> None:
        """Request a photo at the next sync."""
        await self.coordinator.device.async_request_capture_at_sync(self.camera_id)
