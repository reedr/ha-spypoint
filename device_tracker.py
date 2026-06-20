"""Device tracker platform for Spypoint Trail Cameras."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .device import get_camera_coordinate_attributes, get_camera_coordinates
from .entity import SpypointEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpypointConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Spypoint device trackers."""
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
            SpypointCameraTracker(coordinator, camera_id) for camera_id in new_cameras
        )

    _check_cameras()
    config_entry.async_on_unload(coordinator.async_add_listener(_check_cameras))


class SpypointCameraTracker(SpypointEntity, TrackerEntity):
    """GPS location for a Spypoint trail camera."""

    def __init__(
        self,
        coordinator: SpypointCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator, camera_id, "location", entity_name="Location")
        self._attr_name = None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available or self.camera is None:
            return False
        return self._coordinates is not None

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        if self._coordinates is None:
            return None
        return self._coordinates[0]

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        if self._coordinates is None:
            return None
        return self._coordinates[1]

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the device."""
        return SourceType.GPS

    @property
    def _coordinates(self) -> tuple[float, float] | None:
        """Return parsed coordinates for the camera."""
        if self.coordinator.is_position_locked(self.camera_id):
            return self.coordinator.get_locked_coordinates(self.camera_id)
        if not self.camera:
            return None
        return get_camera_coordinates(self.camera)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return coordinate metadata."""
        if self.coordinator.is_position_locked(self.camera_id):
            return self.coordinator.get_locked_coordinate_attributes(self.camera_id)
        if not self.camera:
            return None
        return get_camera_coordinate_attributes(self.camera)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
