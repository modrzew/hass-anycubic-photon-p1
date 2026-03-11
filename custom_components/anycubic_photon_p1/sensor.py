"""Sensor platform for Anycubic Photon P1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AnycubicMqttCoordinator
from .entity import AnycubicEntity


@dataclass(frozen=True, kw_only=True)
class AnycubicSensorEntityDescription(SensorEntityDescription):
    """Describes an Anycubic sensor entity."""

    subtopic: str
    value_fn: Callable[[dict[str, Any]], Any]


SENSOR_DESCRIPTIONS: tuple[AnycubicSensorEntityDescription, ...] = (
    AnycubicSensorEntityDescription(
        key="printer_state",
        translation_key="printer_state",
        subtopic="__combined__",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "online",
            "offline",
            "idle",
            "busy",
            "printing",
            "paused",
            "stopping",
            "complete",
            "monitoring",
            "error",
        ],
        value_fn=lambda d: d.get("state"),
    ),
    AnycubicSensorEntityDescription(
        key="print_progress",
        translation_key="print_progress",
        subtopic="print",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.get("progress"),
    ),
    AnycubicSensorEntityDescription(
        key="current_layer",
        translation_key="current_layer",
        subtopic="print",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: int(d["curr_layer"]) if "curr_layer" in d else None,
    ),
    AnycubicSensorEntityDescription(
        key="total_layers",
        translation_key="total_layers",
        subtopic="print",
        value_fn=lambda d: d.get("total_layers"),
    ),
    AnycubicSensorEntityDescription(
        key="time_remaining",
        translation_key="time_remaining",
        subtopic="print",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: d.get("remain_time"),
    ),
    AnycubicSensorEntityDescription(
        key="print_time",
        translation_key="print_time",
        subtopic="print",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: d.get("print_time"),
    ),
    AnycubicSensorEntityDescription(
        key="current_file",
        translation_key="current_file",
        subtopic="print",
        value_fn=lambda d: d.get("filename"),
    ),
    AnycubicSensorEntityDescription(
        key="resin_usage",
        translation_key="resin_usage",
        subtopic="print",
        native_unit_of_measurement="mL",
        value_fn=lambda d: d.get("supplies_usage"),
    ),
    AnycubicSensorEntityDescription(
        key="model_height",
        translation_key="model_height",
        subtopic="print",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        value_fn=lambda d: d.get("model_hight"),
    ),
    AnycubicSensorEntityDescription(
        key="layer_thickness",
        translation_key="layer_thickness",
        subtopic="print",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        value_fn=lambda d: d.get("z_thick"),
    ),
    AnycubicSensorEntityDescription(
        key="resin_temperature",
        translation_key="resin_temperature",
        subtopic="properties",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("resin_temp"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anycubic Photon P1 sensors."""
    coordinator: AnycubicMqttCoordinator = entry.runtime_data
    async_add_entities(
        AnycubicSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class AnycubicSensor(AnycubicEntity, SensorEntity):
    """Sensor entity for Anycubic Photon P1."""

    entity_description: AnycubicSensorEntityDescription

    def __init__(
        self,
        coordinator: AnycubicMqttCoordinator,
        description: AnycubicSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.printer_info.device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        data = self.coordinator.get_data(self.entity_description.subtopic)
        if data is None:
            return None
        return self.entity_description.value_fn(data)
