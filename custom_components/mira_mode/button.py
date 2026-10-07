"""Stop every outlet."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MiraConfigEntry
from .entity import MiraEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the stop button."""
    async_add_entities([MiraStopButton(entry.runtime_data, "stop")])


class MiraStopButton(MiraEntity, ButtonEntity):
    """Turns every outlet off, leaving the set temperature alone."""

    _attr_translation_key = "stop"

    @property
    def available(self) -> bool:
        """Always offer a stop, even if the last poll failed."""
        return True

    async def async_press(self) -> None:
        """Stop the valve."""
        await self.coordinator.async_set_outlets(0)
