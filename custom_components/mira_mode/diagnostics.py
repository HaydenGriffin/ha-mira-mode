"""Diagnostics download: settings, last decoded state and the raw reply."""

from __future__ import annotations

from typing import Any

from homeassistant.components.bluetooth import async_last_service_info
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_SERIAL
from .coordinator import MiraConfigEntry

TO_REDACT = {CONF_ADDRESS, CONF_SERIAL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MiraConfigEntry
) -> dict[str, Any]:
    """Return what helps decode a bug report without identifying the valve."""
    coordinator = entry.runtime_data
    state = coordinator.data
    advert = async_last_service_info(hass, entry.data[CONF_ADDRESS], connectable=True)
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "connected": coordinator.client.connected,
        "settings": {"temperature": coordinator.temperature, "flow": coordinator.flow},
        "state": None
        if state is None
        else {
            "outlets": state.outlets,
            "temperature": state.temperature,
            "target_temperature": state.target_temperature,
            "flow": state.flow,
            "raw": state.raw.hex(" "),
        },
        "advertisement": None
        if advert is None
        else {
            "name": advert.name,
            "rssi": advert.rssi,
            "service_uuids": advert.service_uuids,
            "manufacturer_data": {
                str(key): value.hex() for key, value in advert.manufacturer_data.items()
            },
        },
    }
