"""One switch per fitted outlet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MiraConfigEntry, MiraCoordinator, outlet_labels
from .entity import MiraEntity, remove_unlabelled_outlets
from .protocol import outlet_bit

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a switch for each labelled outlet and forget unlabelled ones."""
    coordinator = entry.runtime_data
    labels = outlet_labels(entry)
    remove_unlabelled_outlets(hass, entry, Platform.SWITCH, labels, ["outlet_{number}"])
    async_add_entities(
        MiraOutletSwitch(coordinator, number, label) for number, label in labels.items()
    )


class MiraOutletSwitch(MiraEntity, SwitchEntity):
    """Runs one outlet at the current temperature and flow settings.

    Other outlets are left as they are: the valve takes the complete set of
    running outlets in every command, so this sends the current set with
    this outlet added or removed.
    """

    _attr_translation_key = "outlet"

    def __init__(self, coordinator: MiraCoordinator, number: int, label: str) -> None:
        super().__init__(coordinator, f"outlet_{number}")
        self._bit = outlet_bit(number)
        self._attr_translation_placeholders = {"outlet": label}

    @property
    def is_on(self) -> bool:
        """Return whether this outlet is running."""
        return bool(self.coordinator.data.outlets & self._bit)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Open this outlet."""
        await self.coordinator.async_set_outlets(self.coordinator.data.outlets | self._bit)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Close this outlet."""
        await self.coordinator.async_set_outlets(self.coordinator.data.outlets & ~self._bit)

    async def async_start(self, temperature: float | None = None, flow: int | None = None) -> None:
        """Run this outlet at the given temperature and flow (the mira_mode.start action)."""
        if temperature is not None:
            self.coordinator.temperature = temperature
        if flow is not None:
            self.coordinator.flow = flow
        await self.async_turn_on()
        # The number entities show the settings, so refresh them too.
        self.coordinator.async_update_listeners()
