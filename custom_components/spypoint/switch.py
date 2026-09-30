"""Switch platform for Spypoint Trail Cameras."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .entity import SpypointEntity

_LOGGER = logging.getLogger(__name__)

_ENTITY_SUFFIX = "lock_gps_position"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpypointConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Spypoint switches."""
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
            SpypointLockPositionSwitch(coordinator, camera_id)
            for camera_id in new_cameras
        )

    _check_cameras()
    config_entry.async_on_unload(coordinator.async_add_listener(_check_cameras))


class SpypointLockPositionSwitch(SpypointEntity, SwitchEntity, RestoreEntity):
    """Prevent the camera device tracker from updating on API refresh."""

    def __init__(self, coordinator: SpypointCoordinator, camera_id: str) -> None:
        """Initialize the switch."""
        super().__init__(
            coordinator,
            camera_id,
            _ENTITY_SUFFIX,
            entity_name="Lock GPS Position",
        )
        self._attr_translation_key = _ENTITY_SUFFIX

    async def async_added_to_hass(self) -> None:
        """Restore lock state to the coordinator.

        Uses restore_position_locked so a restart re-applies the lock without
        re-freezing the camera at its current API position. Normally the lock
        is already restored from the position lock store before entities are
        added, making this a no-op.
        """
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) and last_state.state == STATE_ON:
            self.coordinator.restore_position_locked(self.camera_id)

    @property
    def is_on(self) -> bool:
        """Return True when the camera location is locked."""
        return self.coordinator.is_position_locked(self.camera_id)

    async def async_turn_on(self, **kwargs) -> None:
        """Lock the device tracker at its current coordinates."""
        self._set_locked(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Allow the device tracker to follow API coordinate updates."""
        self._set_locked(False)

    @callback
    def _set_locked(self, locked: bool) -> None:
        self.coordinator.set_position_locked(self.camera_id, locked)
        self.async_write_ha_state()
