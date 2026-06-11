"""Spypoint entity base class."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SpypointCoordinator

_LOGGER = logging.getLogger(__name__)


def camera_name(camera: dict[str, Any]) -> str:
    """Return the display name for a camera."""
    config = camera.get("config") or {}
    status = camera.get("status") or {}
    return config.get("name") or status.get("model") or camera.get("id", "Spypoint Camera")


def camera_device_info(camera: dict[str, Any]) -> DeviceInfo:
    """Return device registry info for a camera."""
    status = camera.get("status") or {}
    return DeviceInfo(
        identifiers={(DOMAIN, camera["id"])},
        manufacturer=MANUFACTURER,
        model=status.get("model"),
        name=camera_name(camera),
        serial_number=camera.get("ucid"),
        sw_version=status.get("version"),
    )


class SpypointEntity(CoordinatorEntity[SpypointCoordinator]):
    """Base class for Spypoint entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SpypointCoordinator,
        camera_id: str,
        entity_suffix: str,
        *,
        entity_name: str,
    ) -> None:
        """Set up the entity."""
        super().__init__(coordinator)
        self._camera_id = camera_id
        self._entity_suffix = entity_suffix
        camera = coordinator.get_camera(camera_id) or {"id": camera_id}
        self._attr_unique_id = f"{camera_id}_{entity_suffix}"
        self._attr_device_info = camera_device_info(camera)
        self._attr_name = entity_name

    @property
    def camera_id(self) -> str:
        """Return the Spypoint camera id."""
        return self._camera_id

    @property
    def camera(self) -> dict[str, Any] | None:
        """Return the latest camera data."""
        return self.coordinator.get_camera(self._camera_id)
