"""Polling coordinator for a Mira Mode valve."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import CommandRejected, MiraClient, MiraError, ValveNotFound
from .const import (
    DEFAULT_FLOW,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    IDLE_INTERVAL,
    MAX_TEMPERATURE,
    MIN_FLOW,
    MIN_TEMPERATURE,
    RUNNING_INTERVAL,
)
from .protocol import MAX_FLOW, State

_LOGGER = logging.getLogger(__name__)

type MiraConfigEntry = ConfigEntry[MiraCoordinator]


class MiraCoordinator(DataUpdateCoordinator[State]):
    """Reads the valve once a minute when idle and every 10 s while running.

    Also holds the temperature and flow that outlets are started with. While
    the valve runs they follow its own reported values, so a change made on
    the dial or in the phone app is picked up, exactly as the valve itself
    treats "last one wins".
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

    async def _async_update_data(self) -> State:
        try:
            state = await self.client.read_state()
        except ValveNotFound as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="not_found") from err
        except MiraError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
        self._track(state)
        return state

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

    def _track(self, state: State, sync_settings: bool = True) -> None:
        self.update_interval = RUNNING_INTERVAL if state.running else IDLE_INTERVAL
        if not (sync_settings and state.running):
            return
        target = state.target_temperature
        if target is not None and MIN_TEMPERATURE <= target <= MAX_TEMPERATURE:
            self.temperature = target
        if MIN_FLOW <= state.flow <= MAX_FLOW:
            self.flow = state.flow
