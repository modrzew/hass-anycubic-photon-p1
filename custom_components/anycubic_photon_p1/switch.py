"""Switch platform for Anycubic Photon P1."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
    """Set up Anycubic Photon P1 switches."""
    coordinator: AnycubicMqttCoordinator = entry.runtime_data
    async_add_entities([AnycubicAirPurifierSwitch(coordinator)])


class AnycubicAirPurifierSwitch(AnycubicEntity, SwitchEntity):
    """Switch entity for the air purifier."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "air_purifier"

    def __init__(self, coordinator: AnycubicMqttCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._stable_id}_air_purifier"

    @property
    def is_on(self) -> bool | None:
        """Return true if the air purifier is on."""
        # State comes from peripherie query: {"airpure": 0/1}
        data = self.coordinator.get_data("peripherie")
        if data is None:
            return None
        airpure = data.get("airpure")
        if airpure is None:
            return None
        return airpure != 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the air purifier."""
        self.coordinator.publish_command("airpure", "on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the air purifier."""
        self.coordinator.publish_command("airpure", "off")
