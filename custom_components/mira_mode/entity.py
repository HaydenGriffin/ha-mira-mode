"""Base entity for Mira Mode."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, MANUFACTURER, MODEL
from .coordinator import MiraCoordinator


class MiraEntity(CoordinatorEntity[MiraCoordinator]):
    """An entity belonging to one valve."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MiraCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        address: str = entry.data[CONF_ADDRESS]
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=entry.data.get(CONF_SERIAL),
        )
