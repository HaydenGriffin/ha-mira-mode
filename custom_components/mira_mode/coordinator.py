"""Polling coordinator for a Mira Mode valve."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import CommandRejected, MiraClient, MiraError, Reading, ValveNotFound
from .const import (
    CONF_OUTLET_LABELS,
    DEFAULT_FLOW,
    DEFAULT_OUTLET_LABELS,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    EVENT_USAGE,
    IDLE_INTERVAL,
    KWH_PER_LITRE_KELVIN,
    MAX_TEMPERATURE,
    MIN_FLOW,
    MIN_TEMPERATURE,
    RUNNING_INTERVAL,
    SETTINGS_INTERVAL,
)
from .protocol import MAX_FLOW, Inlets, OutletSettings, State, Usage

_LOGGER = logging.getLogger(__name__)

type MiraConfigEntry = ConfigEntry[MiraCoordinator]


def outlet_labels(entry: MiraConfigEntry) -> dict[int, str]:
    """Outlet number to label, for every outlet given a label in the options."""
    labels: dict[int, str] = {}
    for number, key in enumerate(CONF_OUTLET_LABELS, start=1):
        label = str(entry.options.get(key, DEFAULT_OUTLET_LABELS[key])).strip()
        if label:
            labels[number] = label
    return labels


def heating_energy(usage: Usage, cold: float | None) -> float | None:
    """Energy (kWh) that warmed a run's water from the cold inlet to its blend temperature."""
    if cold is None or usage.temperature <= cold:
        return None
    return round(usage.litres * KWH_PER_LITRE_KELVIN * (usage.temperature - cold), 3)


@dataclass(frozen=True)
class CountedRun:
    """A finished run, as added to the totals."""

    usage: Usage
    #: Heating energy in kWh, or None without a cold inlet reading.
    energy: float | None


class MiraCoordinator(DataUpdateCoordinator[State]):
    """Reads the valve once a minute when idle and every 10 s while running.

    Also holds the temperature and flow that outlets are started with. While
    the valve runs they follow its own reported values, so a change made on
    the dial or in the phone app is picked up, exactly as the valve itself
    treats "last one wins".

    The extra reads (inlets, last usage, outlet settings) live alongside the
    state and keep their last value when the valve doesn't answer them.
    """

    config_entry: MiraConfigEntry

    def __init__(self, hass: HomeAssistant, entry: MiraConfigEntry, client: MiraClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=IDLE_INTERVAL,
        )
        self.client = client
        self.temperature = DEFAULT_TEMPERATURE
        self.flow = DEFAULT_FLOW
        self.inlets: Inlets | None = None
        self.last_usage: Usage | None = None
        self.outlet_settings: dict[int, OutletSettings] = {}
        #: Goes up by one for each finished run; totals add ``last_run`` when it moves.
        self.runs_counted = 0
        self.last_run: CountedRun | None = None
        self._settings_due: datetime | None = None

    async def _async_update_data(self) -> State:
        outlets = self._outlets_due()
        try:
            reading = await self.client.read(outlets)
        except ValveNotFound as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="not_found") from err
        except MiraError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
        if outlets:
            self._settings_due = dt_util.utcnow() + SETTINGS_INTERVAL
        self._remember(reading)
        self._track(reading.state)
        return reading.state

    async def async_set_outlets(self, outlets: int) -> State:
        """Run exactly ``outlets`` at the current settings (0 stops everything).

        Raises unless the valve acknowledges and then reads back as doing
        what was asked: this is real hot water, so silence is not success.
        """
        temperature = self.temperature if outlets else 0.0
        flow = self.flow if outlets else 0
        try:
            state = await self.client.set_outlets(outlets, temperature, flow)
        except CommandRejected as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={"error": str(err)},
            ) from err
        except ValveNotFound as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_found"
            ) from err
        except MiraError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
        # The reply to a command may predate the new settings, so only polls
        # are allowed to pull temperature and flow from the valve.
        self._track(state, sync_settings=False)
        self.async_set_updated_data(state)
        if state.outlets != outlets:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_following")
        return state

    async def async_apply_settings(self) -> None:
        """Push new temperature/flow settings to the valve if it is running."""
        if self.data is not None and self.data.running:
            await self.async_set_outlets(self.data.outlets)

    async def async_shutdown(self) -> None:
        """Release the Bluetooth connection with the coordinator."""
        await super().async_shutdown()
        await self.client.disconnect()

    def _outlets_due(self) -> list[int]:
        """Labelled outlets, when their settings are due a read; else none."""
        if self._settings_due is not None and dt_util.utcnow() < self._settings_due:
            return []
        return list(outlet_labels(self.config_entry))

    def _remember(self, reading: Reading) -> None:
        self.inlets = reading.inlets or self.inlets
        self.outlet_settings.update(reading.outlet_settings)
        usage = reading.last_usage
        if usage is None or reading.state.running:
            return
        # The first record seen (after a restart, say) is only a baseline.
        # Two identical runs in a row look like one and are counted once.
        if self.last_usage is not None and usage != self.last_usage:
            self._count(usage)
        self.last_usage = usage

    def _count(self, usage: Usage) -> None:
        energy = heating_energy(usage, self.inlets.cold if self.inlets else None)
        self.last_run = CountedRun(usage, energy)
        self.runs_counted += 1
        devices = dr.async_entries_for_config_entry(
            dr.async_get(self.hass), self.config_entry.entry_id
        )
        self.hass.bus.async_fire(
            EVENT_USAGE,
            {
                "device_id": devices[0].id if devices else None,
                "seconds": usage.seconds,
                "litres": usage.litres,
                "temperature": usage.temperature,
                "energy_kwh": energy,
            },
        )

    def _track(self, state: State, sync_settings: bool = True) -> None:
        self.update_interval = RUNNING_INTERVAL if state.running else IDLE_INTERVAL
        if not (sync_settings and state.running):
            return
        target = state.target_temperature
        if target is not None and MIN_TEMPERATURE <= target <= MAX_TEMPERATURE:
            self.temperature = target
        if MIN_FLOW <= state.flow <= MAX_FLOW:
            self.flow = state.flow
