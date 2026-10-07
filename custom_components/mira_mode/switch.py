"""One switch per fitted outlet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_OUTLET_LABELS, DEFAULT_OUTLET_LABELS, DOMAIN
from .coordinator import MiraConfigEntry, MiraCoordinator
from .entity import MiraEntity
from .protocol import outlet_bit

PARALLEL_UPDATES = 1


def outlet_labels(entry: MiraConfigEntry) -> dict[int, str]:
    """Outlet number to label, for every outlet given a label in the options."""
    labels: dict[int, str] = {}
    for number, key in enumerate(CONF_OUTLET_LABELS, start=1):
        label = str(entry.options.get(key, DEFAULT_OUTLET_LABELS[key])).strip()
        if label:
            labels[number] = label
    return labels


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a switch for each labelled outlet and forget unlabelled ones."""
    coordinator = entry.runtime_data
    labels = outlet_labels(entry)
    _remove_unlabelled(hass, entry, labels)
    async_add_entities(
        MiraOutletSwitch(coordinator, number, label) for number, label in labels.items()
    )


def _remove_unlabelled(hass: HomeAssistant, entry: MiraConfigEntry, labels: dict[int, str]) -> None:
    registry = er.async_get(hass)
    for number in range(1, len(CONF_OUTLET_LABELS) + 1):
        if number in labels:
            continue
        unique_id = MiraOutletSwitch.unique_id_for(entry, number)
        if entity_id := registry.async_get_entity_id(Platform.SWITCH, DOMAIN, unique_id):
            registry.async_remove(entity_id)


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
        self._attr_name = label

    @staticmethod
    def unique_id_for(entry: MiraConfigEntry, number: int) -> str:
        """Return the unique id of outlet ``number``'s switch (matches MiraEntity)."""
        return f"{entry.data[CONF_ADDRESS]}_outlet_{number}"

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
