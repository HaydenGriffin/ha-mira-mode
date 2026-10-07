"""Temperature and flow that outlets run at."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_TEMPERATURE, MIN_FLOW, MIN_TEMPERATURE, TEMPERATURE_STEP
from .coordinator import MiraConfigEntry, MiraCoordinator
from .entity import MiraEntity
from .protocol import MAX_FLOW

PARALLEL_UPDATES = 1

DESCRIPTIONS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=MIN_TEMPERATURE,
        native_max_value=MAX_TEMPERATURE,
        native_step=TEMPERATURE_STEP,
        mode=NumberMode.SLIDER,
    ),
    NumberEntityDescription(
        key="flow",
        translation_key="flow",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=MIN_FLOW,
        native_max_value=MAX_FLOW,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the temperature and flow settings."""
    async_add_entities(
        MiraSettingNumber(entry.runtime_data, description) for description in DESCRIPTIONS
    )


class MiraSettingNumber(MiraEntity, RestoreNumber):
    """A run setting: applied live while running, used for the next start otherwise.

    While the valve runs, the value follows what the valve reports, so a
    change on the dial or in the phone app shows up here too.
    """

    def __init__(self, coordinator: MiraCoordinator, description: NumberEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Settings can be changed even while the valve is out of reach."""
        return True

    @property
    def native_value(self) -> float:
        """Return the current setting."""
        if self.entity_description.key == "flow":
            return self.coordinator.flow
        return self.coordinator.temperature

    async def async_added_to_hass(self) -> None:
        """Restore the last setting, unless the running valve already set it."""
        await super().async_added_to_hass()
        if self.coordinator.data.running:
            return
        last = await self.async_get_last_number_data()
        if last is None or last.native_value is None:
            return
        if self.native_min_value <= last.native_value <= self.native_max_value:
            self._set(last.native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Change the setting, and the running valve with it."""
        self._set(value)
        self.async_write_ha_state()
        await self.coordinator.async_apply_settings()

    def _set(self, value: float) -> None:
        if self.entity_description.key == "flow":
            self.coordinator.flow = int(value)
        else:
            self.coordinator.temperature = float(value)
