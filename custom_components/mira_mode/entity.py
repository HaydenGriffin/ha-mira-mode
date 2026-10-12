"""Base entity for Mira Mode."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_OUTLET_LABELS, CONF_SERIAL, DOMAIN, MANUFACTURER, MODEL
from .coordinator import MiraConfigEntry, MiraCoordinator


class MiraEntity(CoordinatorEntity[MiraCoordinator]):
    """An entity belonging to one valve."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MiraCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        address: str = entry.data[CONF_ADDRESS]
        self._attr_unique_id = self.unique_id_for(entry, key)
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=entry.data.get(CONF_SERIAL),
        )

    @staticmethod
    def unique_id_for(entry: MiraConfigEntry, key: str) -> str:
        """Return the unique id of the entity with ``key``."""
        return f"{entry.data[CONF_ADDRESS]}_{key}"


def remove_unlabelled_outlets(
    hass: HomeAssistant,
    entry: MiraConfigEntry,
    platform: Platform,
    labels: dict[int, str],
    keys: Iterable[str],
) -> None:
    """Forget this platform's per-outlet entities for outlets without a label.

    ``keys`` are entity keys with ``{number}`` standing for the outlet number.
    """
    registry = er.async_get(hass)
    for number in range(1, len(CONF_OUTLET_LABELS) + 1):
        if number in labels:
            continue
        for key in keys:
            unique_id = MiraEntity.unique_id_for(entry, key.format(number=number))
            if entity_id := registry.async_get_entity_id(platform, DOMAIN, unique_id):
                registry.async_remove(entity_id)
