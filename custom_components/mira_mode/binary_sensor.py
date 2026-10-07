"""Whether any outlet is running."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MiraConfigEntry
from .entity import MiraEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the running sensor."""
    async_add_entities([MiraRunningSensor(entry.runtime_data, "running")])


class MiraRunningSensor(MiraEntity, BinarySensorEntity):
    """On while water is flowing from any outlet."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "running"

    @property
    def is_on(self) -> bool | None:
        """Return whether any outlet is open."""
        return self.coordinator.data.running
