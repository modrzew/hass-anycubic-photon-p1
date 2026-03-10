"""The Anycubic Photon P1 integration."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AnycubicApi, AnycubicApiError
from .coordinator import AnycubicMqttCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type AnycubicPhotonP1ConfigEntry = ConfigEntry[AnycubicMqttCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: AnycubicPhotonP1ConfigEntry
) -> bool:
    """Set up Anycubic Photon P1 from a config entry."""
    host = entry.data[CONF_HOST]
    session = async_get_clientsession(hass)
    api = AnycubicApi(host, session)

    try:
        printer_info = await api.get_info()
    except (AnycubicApiError, aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"Cannot connect to printer at {host}") from err

    coordinator = AnycubicMqttCoordinator(hass, entry, api, printer_info)
    await coordinator.async_start()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AnycubicPhotonP1ConfigEntry
) -> bool:
    """Unload a config entry."""
    coordinator: AnycubicMqttCoordinator = entry.runtime_data
    await coordinator.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
