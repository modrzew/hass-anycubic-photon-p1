"""Light platform for Anycubic Photon P1."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import (
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AnycubicMqttCoordinator
from .entity import AnycubicEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Anycubic Photon P1 light."""
    coordinator: AnycubicMqttCoordinator = entry.runtime_data
    async_add_entities([AnycubicLight(coordinator)])


class AnycubicLight(AnycubicEntity, LightEntity):
    """Light entity for the Anycubic Photon P1 printer.

    The desktop app uses publishSetLightStatus(bool) for on/off control.
    """

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}
    _attr_translation_key = "light"

    def __init__(self, coordinator: AnycubicMqttCoordinator) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._stable_id}_light"

    @property
    def is_on(self) -> bool | None:
        """Return true if the light is on."""
        data = self.coordinator.get_data("light")
        if data is None:
            return None
        # Response format: {"lights": [{"type": 3, "status": 0}]}
        lights = data.get("lights")
        if isinstance(lights, list) and lights:
            return lights[0].get("status", 0) != 0
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        self.coordinator.publish_command("light", "control", {"type": 3, "status": 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        self.coordinator.publish_command("light", "control", {"type": 3, "status": 0})
