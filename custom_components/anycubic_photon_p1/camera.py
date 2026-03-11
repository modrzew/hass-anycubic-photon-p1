"""Camera platform for Anycubic Photon P1."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
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
    """Set up the Anycubic Photon P1 camera."""
    coordinator: AnycubicMqttCoordinator = entry.runtime_data
    async_add_entities([AnycubicCamera(coordinator)])


class AnycubicCamera(AnycubicEntity, Camera):
    """Camera entity for the Anycubic Photon P1 printer."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_translation_key = "camera"

    def __init__(self, coordinator: AnycubicMqttCoordinator) -> None:
        """Initialize the camera."""
        AnycubicEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{coordinator.printer_info.device_id}_camera"

    async def stream_source(self) -> str | None:
        """Return the stream source URL."""
        if not self.coordinator.available:
            return None
        return self.coordinator.stream_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        if not self.coordinator.available:
            return None
        return await async_get_image(
            self.hass,
            self.coordinator.stream_url,
            width=width,
            height=height,
        )
