"""Water temperature and flow, the supply, the last run, totals and the valve's limits."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    Platform,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import CountedRun, MiraConfigEntry, MiraCoordinator, outlet_labels
from .entity import MiraEntity, remove_unlabelled_outlets
from .protocol import OutletSettings, litres_per_minute

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MiraSensorEntityDescription(SensorEntityDescription):
    """A sensor for the whole valve."""

    value_fn: Callable[[MiraCoordinator], StateType]


@dataclass(frozen=True, kw_only=True)
class MiraOutletSensorEntityDescription(SensorEntityDescription):
    """A setting of one outlet."""

    value_fn: Callable[[OutletSettings], StateType]


@dataclass(frozen=True, kw_only=True)
class MiraTotalSensorEntityDescription(SensorEntityDescription):
    """A running total; ``amount_fn`` is what one finished run adds to it."""

    amount_fn: Callable[[CountedRun], float | None]


SENSORS: tuple[MiraSensorEntityDescription, ...] = (
    # Near room temperature while idle; climbs towards the target once an
    # outlet opens.
    MiraSensorEntityDescription(
        key="water_temperature",
        translation_key="water_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: c.data.temperature,
    ),
    MiraSensorEntityDescription(
        key="flow_rate",
        translation_key="flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: c.data.flow_rate,
    ),
    MiraSensorEntityDescription(
        key="cold_inlet_temperature",
        translation_key="cold_inlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: c.inlets.cold if c.inlets else None,
    ),
    MiraSensorEntityDescription(
        key="last_run_duration",
        translation_key="last_run_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda c: c.last_usage.seconds if c.last_usage else None,
    ),
    MiraSensorEntityDescription(
        key="last_run_water",
        translation_key="last_run_water",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        value_fn=lambda c: c.last_usage.litres if c.last_usage else None,
    ),
    MiraSensorEntityDescription(
        key="last_run_temperature",
        translation_key="last_run_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda c: c.last_usage.temperature if c.last_usage else None,
    ),
)

#: Experimental: which bytes carry the hot inlet is not settled yet.
HOT_INLET = MiraSensorEntityDescription(
    key="hot_inlet_temperature",
    translation_key="hot_inlet_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
    entity_registry_enabled_default=False,
    value_fn=lambda c: c.inlets.hot if c.inlets else None,
)

OUTLET_SENSORS: tuple[MiraOutletSensorEntityDescription, ...] = (
    MiraOutletSensorEntityDescription(
        key="max_temperature",
        translation_key="max_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.max_temperature,
    ),
    MiraOutletSensorEntityDescription(
        key="max_duration",
        translation_key="max_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.max_duration,
    ),
    MiraOutletSensorEntityDescription(
        key="default_flow",
        translation_key="default_flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: litres_per_minute(s.default_flow),
    ),
)

TOTALS: tuple[MiraTotalSensorEntityDescription, ...] = (
    MiraTotalSensorEntityDescription(
        key="water_used",
        translation_key="water_used",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        amount_fn=lambda run: run.usage.litres,
    ),
    MiraTotalSensorEntityDescription(
        key="heating_energy",
        translation_key="heating_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        amount_fn=lambda run: run.energy,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the valve's sensors, and the settings of each labelled outlet."""
    coordinator = entry.runtime_data
    labels = outlet_labels(entry)
    outlet_keys = [f"outlet_{{number}}_{description.key}" for description in OUTLET_SENSORS]
    remove_unlabelled_outlets(hass, entry, Platform.SENSOR, labels, outlet_keys)
    entities: list[SensorEntity] = [MiraSensor(coordinator, d) for d in SENSORS]
    entities.append(MiraHotInletSensor(coordinator, HOT_INLET))
    entities += [MiraTotalSensor(coordinator, d) for d in TOTALS]
    entities += [
        MiraOutletSensor(coordinator, d, number, label)
        for number, label in labels.items()
        for d in OUTLET_SENSORS
    ]
    async_add_entities(entities)


class MiraSensor(MiraEntity, SensorEntity):
    """A value read from the valve."""

    entity_description: MiraSensorEntityDescription

    def __init__(
        self, coordinator: MiraCoordinator, description: MiraSensorEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator)


class MiraHotInletSensor(MiraSensor):
    """Hot supply temperature; the raw reply rides along until the decode is proven."""

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the undecoded inlet reply."""
        inlets = self.coordinator.inlets
        return None if inlets is None else {"raw": inlets.raw.hex(" ")}


class MiraOutletSensor(MiraEntity, SensorEntity):
    """A limit or default the valve applies to one outlet, set in the Mira app."""

    entity_description: MiraOutletSensorEntityDescription

    def __init__(
        self,
        coordinator: MiraCoordinator,
        description: MiraOutletSensorEntityDescription,
        number: int,
        label: str,
    ) -> None:
        super().__init__(coordinator, f"outlet_{number}_{description.key}")
        self.entity_description = description
        self._number = number
        self._attr_translation_placeholders = {"outlet": label}

    @property
    def native_value(self) -> StateType:
        """Return the setting, once it has been read."""
        settings = self.coordinator.outlet_settings.get(self._number)
        return None if settings is None else self.entity_description.value_fn(settings)


class MiraTotalSensor(MiraEntity, RestoreSensor):
    """Adds each finished run once, and carries on across restarts.

    Daily or monthly figures come from long-term statistics or a utility
    meter on top.
    """

    entity_description: MiraTotalSensorEntityDescription

    def __init__(
        self, coordinator: MiraCoordinator, description: MiraTotalSensorEntityDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._total = 0.0
        self._counted = coordinator.runs_counted

    @property
    def native_value(self) -> float:
        """Return the total."""
        return self._total

    async def async_added_to_hass(self) -> None:
        """Restore the total from before the restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            with suppress(TypeError, ValueError):
                self._total = float(last.native_value)  # type: ignore[arg-type]

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.runs_counted != self._counted:
            self._counted = self.coordinator.runs_counted
            run = self.coordinator.last_run
            amount = None if run is None else self.entity_description.amount_fn(run)
            if amount is not None:
                self._total = round(self._total + amount, 3)
        super()._handle_coordinator_update()
