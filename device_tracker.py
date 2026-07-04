"""Device tracker platform for Spypoint Trail Cameras."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .device import get_camera_coordinate_attributes, get_camera_coordinates
from .entity import SpypointEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_COORDINATE_ATTRIBUTES = frozenset({"geohash", "date_time"})


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


class SpypointCameraTracker(SpypointEntity, TrackerEntity, RestoreEntity):
    """GPS location for a Spypoint trail camera."""

    _attr_latitude: float | None = None
    _attr_longitude: float | None = None
    _attr_source_type = SourceType.GPS

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
        if not super().available:
            return False
        return (
            self._attr_latitude is not None and self._attr_longitude is not None
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last known location."""
        await super().async_added_to_hass()
        if self._attr_latitude is None and self._attr_longitude is None:
            if (state := await self.async_get_last_state()) is not None:
                attrs = state.attributes
                self._attr_latitude = attrs.get(ATTR_LATITUDE)
                self._attr_longitude = attrs.get(ATTR_LONGITUDE)
                if restored_attrs := {
                    key: value
                    for key, value in attrs.items()
                    if key in _COORDINATE_ATTRIBUTES
                }:
                    self._attr_extra_state_attributes = restored_attrs
        self._async_update_coordinates()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._async_update_coordinates()
        self.async_write_ha_state()

    @callback
    def _async_update_coordinates(self) -> None:
        """Update coordinates from the coordinator or API."""
        if self.coordinator.is_position_locked(self.camera_id):
            if coords := self.coordinator.get_locked_coordinates(self.camera_id):
                self._attr_latitude, self._attr_longitude = coords
            if attrs := self.coordinator.get_locked_coordinate_attributes(
                self.camera_id
            ):
                self._attr_extra_state_attributes = attrs
            return

        if not self.camera:
            return

        if coords := get_camera_coordinates(self.camera):
            self._attr_latitude, self._attr_longitude = coords
        if attrs := get_camera_coordinate_attributes(self.camera):
            self._attr_extra_state_attributes = attrs
