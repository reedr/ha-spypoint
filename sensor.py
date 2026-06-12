"""Sensor platform for Spypoint Trail Cameras."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SpypointConfigEntry, SpypointCoordinator
from .entity import (
    SpypointEntity,
    last_update,
    photo_count,
    photo_limit,
)

_LOGGER = logging.getLogger(__name__)


def _battery_value(camera: dict[str, Any]) -> int | None:
    """Extract battery percentage from camera data."""
    status = camera.get("status") or {}
    power_sources = status.get("powerSources") or []
    if power_sources:
        percentage = power_sources[0].get("percentage")
        if percentage is not None:
            return int(percentage)
    batteries = status.get("batteries") or []
    for value in reversed(batteries):
        if value:
            return int(value)
    return None


def _signal_value(camera: dict[str, Any]) -> int | None:
    """Extract signal strength in dBm."""
    signal = (camera.get("status") or {}).get("signal") or {}
    if (value := signal.get("dBm")) is None:
        return None
    return int(value)


def _temperature_value(camera: dict[str, Any]) -> float | None:
    """Extract temperature."""
    temperature = (camera.get("status") or {}).get("temperature") or {}
    if (value := temperature.get("value")) is None:
        return None
    return float(value)


def _memory_used_value(camera: dict[str, Any]) -> int | None:
    """Extract used memory in MB."""
    memory = (camera.get("status") or {}).get("memory") or {}
    if (value := memory.get("used")) is None:
        return None
    return int(value)


def _sd_card_used_percent_value(camera: dict[str, Any]) -> float | None:
    """Extract SD card used percentage."""
    memory = (camera.get("status") or {}).get("memory") or {}
    used = memory.get("used")
    size = memory.get("size")
    if used is None or not size:
        return None
    return round(float(used) / float(size) * 100, 1)


def _photo_count_value(camera: dict[str, Any]) -> int | None:
    """Extract photos used in the current billing cycle."""
    return photo_count(camera)


def _photo_limit_value(camera: dict[str, Any]) -> int | None:
    """Extract monthly photo limit."""
    return photo_limit(camera)


def _last_update_value(camera: dict[str, Any]) -> datetime | None:
    """Extract the last camera status update time."""
    return last_update(camera)


def _notifications(camera: dict[str, Any]) -> list[Any]:
    """Extract camera notifications."""
    status = camera.get("status") or {}
    notifications = status.get("notifications")
    if isinstance(notifications, list):
        return notifications
    return []


def _alarms_value(camera: dict[str, Any]) -> int:
    """Return the number of active notifications."""
    return len(_notifications(camera))


def _alarms_attributes(camera: dict[str, Any]) -> dict[str, Any]:
    """Return notification details as attributes."""
    return {"notifications": _notifications(camera)}


@dataclass(frozen=True, kw_only=True)
class SpypointSensorEntityDescription(SensorEntityDescription):
    """Describe a Spypoint sensor entity."""

    value_fn: Any
    entity_name: str
    attributes_fn: Any | None = None


SENSOR_DESCRIPTIONS: tuple[SpypointSensorEntityDescription, ...] = (
    SpypointSensorEntityDescription(
        key="battery",
        entity_name="Battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_battery_value,
    ),
    SpypointSensorEntityDescription(
        key="signal_strength",
        entity_name="Signal strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_signal_value,
    ),
    SpypointSensorEntityDescription(
        key="temperature",
        entity_name="Temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_temperature_value,
    ),
    SpypointSensorEntityDescription(
        key="memory_used",
        entity_name="Memory used",
        translation_key="memory_used",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="MB",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_memory_used_value,
    ),
    SpypointSensorEntityDescription(
        key="sd_card_used",
        entity_name="SD card used",
        translation_key="sd_card_used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sd_card_used_percent_value,
    ),
    SpypointSensorEntityDescription(
        key="photo_count",
        entity_name="Photos used this cycle",
        translation_key="photo_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_photo_count_value,
    ),
    SpypointSensorEntityDescription(
        key="photo_limit",
        entity_name="Photo limit",
        translation_key="photo_limit",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_photo_limit_value,
    ),
    SpypointSensorEntityDescription(
        key="last_update",
        entity_name="Last update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_update_value,
    ),
    SpypointSensorEntityDescription(
        key="alarms",
        entity_name="Alarms",
        translation_key="alarms",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_alarms_value,
        attributes_fn=_alarms_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpypointConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Spypoint sensors."""
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
            SpypointSensor(coordinator, camera_id, description)
            for camera_id in new_cameras
            for description in SENSOR_DESCRIPTIONS
        )

    _check_cameras()
    config_entry.async_on_unload(coordinator.async_add_listener(_check_cameras))


class SpypointSensor(SpypointEntity, SensorEntity):
    """Representation of a Spypoint sensor."""

    entity_description: SpypointSensorEntityDescription

    def __init__(
        self,
        coordinator: SpypointCoordinator,
        camera_id: str,
        description: SpypointSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator,
            camera_id,
            description.key,
            entity_name=description.entity_name,
        )
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> datetime | int | float | None:
        """Return the sensor value."""
        if not (camera := self.camera):
            return None
        return self.entity_description.value_fn(camera)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.entity_description.attributes_fn:
            return None
        camera = self.camera or {}
        return self.entity_description.attributes_fn(camera)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
