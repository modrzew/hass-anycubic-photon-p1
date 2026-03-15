"""Button platform for Anycubic Photon P1."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AnycubicMqttCoordinator
from .entity import AnycubicEntity


@dataclass(frozen=True, kw_only=True)
class AnycubicButtonEntityDescription(ButtonEntityDescription):
    """Describes an Anycubic button entity."""

    subtopic: str
    action: str


BUTTON_DESCRIPTIONS: tuple[AnycubicButtonEntityDescription, ...] = (
    AnycubicButtonEntityDescription(
        key="pause_print",
        translation_key="pause_print",
        subtopic="print",
        action="pause",
    ),
    AnycubicButtonEntityDescription(
        key="resume_print",
        translation_key="resume_print",
        subtopic="print",
        action="resume",
    ),
    AnycubicButtonEntityDescription(
        key="stop_print",
        translation_key="stop_print",
        subtopic="print",
        action="stop",
        device_class=ButtonDeviceClass.RESTART,
    ),
    AnycubicButtonEntityDescription(
        key="cancel_exposure_test",
        translation_key="cancel_exposure_test",
        subtopic="exposure",
        action="cancel",
    ),
    AnycubicButtonEntityDescription(
        key="cancel_residual_cleaning",
        translation_key="cancel_residual_cleaning",
        subtopic="residual",
        action="cancel",
    ),
    AnycubicButtonEntityDescription(
        key="reset_release_film_counter",
        translation_key="reset_release_film_counter",
        subtopic="releaseFilm",
        action="reset",
    ),
    AnycubicButtonEntityDescription(
        key="turn_off_motors",
        translation_key="turn_off_motors",
        subtopic="axis",
        action="turnOff",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anycubic Photon P1 buttons."""
    coordinator: AnycubicMqttCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        AnycubicRefreshButton(coordinator),
        *(
            AnycubicButton(coordinator, description)
            for description in BUTTON_DESCRIPTIONS
        ),
    ]
    async_add_entities(entities)


class AnycubicButton(AnycubicEntity, ButtonEntity):
    """Button entity for Anycubic Photon P1."""

    entity_description: AnycubicButtonEntityDescription

    def __init__(
        self,
        coordinator: AnycubicMqttCoordinator,
        description: AnycubicButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._stable_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle the button press."""
        self.coordinator.publish_command(
            self.entity_description.subtopic,
            self.entity_description.action,
        )


class AnycubicRefreshButton(AnycubicEntity, ButtonEntity):
    """Button entity that requests a full state refresh."""

    _attr_translation_key = "refresh_state"

    def __init__(self, coordinator: AnycubicMqttCoordinator) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._stable_id}_refresh_state"

    async def async_press(self) -> None:
        """Request a full state refresh from the printer."""
        self.coordinator.request_refresh()
