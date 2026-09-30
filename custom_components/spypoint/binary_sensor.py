"""Binary sensor platform for Spypoint Trail Cameras."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .camera_data import (
    camera_stale_after_seconds,
    camera_status_refresh_interval_seconds,
    last_update,
)
from .const import STALE_RECHECK_INTERVAL
from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .entity import SpypointEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpypointConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Spypoint binary sensors."""
    coordinator = config_entry.runtime_data
    known_cameras: set[str] = set()

    def _check_cameras() -> None:
        if not coordinator.data:
            return
        current_cameras = set(coordinator.data.get("cameras", {}))
        # Forget cameras whose device has been deleted, so one that comes back
        # is given new entities; a camera merely missing from the API keeps its
        # existing ones.
        known_cameras.intersection_update(
            current_cameras | coordinator.registered_camera_ids()
        )
        new_cameras = current_cameras - known_cameras
        if not new_cameras:
            return
        known_cameras.update(new_cameras)
        async_add_entities(
            SpypointStaleBinarySensor(coordinator, camera_id)
            for camera_id in new_cameras
        )

    _check_cameras()
    config_entry.async_on_unload(coordinator.async_add_listener(_check_cameras))


class SpypointStaleBinarySensor(SpypointEntity, BinarySensorEntity):
    """Reports a camera that has not checked in on schedule."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: SpypointCoordinator, camera_id: str
    ) -> None:
        """Initialize the stale sensor."""
        super().__init__(coordinator, camera_id, "stale", entity_name="Stale")
        self._attr_translation_key = "stale"

    async def async_added_to_hass(self) -> None:
        """Start the periodic re-evaluation timer.

        Staleness is a function of elapsed time rather than of new API data,
        and the coordinator only notifies listeners when the payload changes,
        so this entity needs its own clock to flip on schedule.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_recheck, STALE_RECHECK_INTERVAL
            )
        )

    @callback
    def _async_recheck(self, now: datetime) -> None:
        """Re-evaluate the elapsed time since the last check-in."""
        self.async_write_ha_state()

    def _stale_after_seconds(self, camera: dict[str, Any]) -> int:
        """Return the staleness threshold for this camera."""
        return camera_stale_after_seconds(camera, self.coordinator.config_entry)

    @property
    def is_on(self) -> bool | None:
        """Return True when the camera is overdue for a check-in."""
        if not (camera := self.camera):
            return None
        if (updated := last_update(camera)) is None:
            return None
        elapsed = (dt_util.utcnow() - updated).total_seconds()
        return elapsed > self._stale_after_seconds(camera)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the values behind the verdict."""
        if not (camera := self.camera):
            return None
        updated = last_update(camera)
        return {
            "last_update": updated.isoformat() if updated else None,
            "expected_interval_seconds": camera_status_refresh_interval_seconds(
                camera, self.coordinator.config_entry
            ),
            "stale_after_seconds": self._stale_after_seconds(camera),
            "seconds_since_update": (
                int((dt_util.utcnow() - updated).total_seconds()) if updated else None
            ),
        }
