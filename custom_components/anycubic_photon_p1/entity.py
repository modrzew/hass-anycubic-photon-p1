"""Base entity for Anycubic Photon P1."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .coordinator import AnycubicMqttCoordinator


class AnycubicEntity(Entity):
    """Base entity for Anycubic Photon P1."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: AnycubicMqttCoordinator) -> None:
        """Initialize the entity."""
        self.coordinator = coordinator
        info = coordinator.printer_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.device_id)},
            name=info.name,
            manufacturer="Anycubic",
            model=info.model,
            sw_version=info.firmware or None,
            connections={(CONNECTION_NETWORK_MAC, info.mac)} if info.mac else set(),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.available

    async def async_added_to_hass(self) -> None:
        """Register dispatcher listener when added to hass."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(entry_id=self.coordinator.entry.entry_id),
                self.async_write_ha_state,
            )
        )
