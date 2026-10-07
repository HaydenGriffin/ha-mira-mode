"""Mira Mode digital showers over Bluetooth LE.

Talks to current-generation Mira Mode valves through Home Assistant's
Bluetooth stack, so a local adapter or a connectable proxy (for example
ESPHome) can carry the connection. Protocol credit: ryan-shaw/mira-mode-control
(MIT); see NOTICE.
"""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .client import MiraClient
from .const import DOMAIN
from .coordinator import MiraConfigEntry, MiraCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MiraConfigEntry) -> bool:
    """Set up one valve."""
    client = MiraClient(hass, entry.data[CONF_ADDRESS])
    coordinator = MiraCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MiraConfigEntry) -> bool:
    """Unload a valve and drop its connection."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
