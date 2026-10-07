"""Measured water temperature."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
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
    """Add the water temperature sensor."""
    async_add_entities([MiraWaterTemperatureSensor(entry.runtime_data, "water_temperature")])


class MiraWaterTemperatureSensor(MiraEntity, SensorEntity):
    """Temperature of the water at the valve's mixer.

    Reads near room temperature while idle and climbs towards the target
    once an outlet opens.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_translation_key = "water_temperature"

    @property
    def native_value(self) -> float:
        """Return the measured temperature."""
        return self.coordinator.data.temperature
