"""Actions for Mira Mode."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import service
from homeassistant.helpers.typing import VolDictType
import voluptuous as vol

from .const import (
    ATTR_FLOW,
    ATTR_TEMPERATURE,
    DOMAIN,
    MAX_TEMPERATURE,
    MIN_FLOW,
    MIN_TEMPERATURE,
    SERVICE_START,
)
from .protocol import MAX_FLOW

START_SCHEMA: VolDictType = {
    vol.Optional(ATTR_TEMPERATURE): vol.All(
        vol.Coerce(float), vol.Range(min=MIN_TEMPERATURE, max=MAX_TEMPERATURE)
    ),
    vol.Optional(ATTR_FLOW): vol.All(vol.Coerce(int), vol.Range(min=MIN_FLOW, max=MAX_FLOW)),
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register mira_mode.start, which targets an outlet switch."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START,
        entity_domain=Platform.SWITCH,
        schema=START_SCHEMA,
        func="async_start",
    )
