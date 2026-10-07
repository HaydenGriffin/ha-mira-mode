"""Config flow for Mira Mode: discover or enter a valve, bond with it, prove the link."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)
import voluptuous as vol

from .client import MiraClient, PairingFailed, ValveInfo, ValveNotFound, ValveUnreachable
from .const import CONF_OUTLET_LABELS, CONF_SERIAL, DEFAULT_OUTLET_LABELS, DOMAIN
from .coordinator import MiraConfigEntry
from .protocol import LEGACY_SERVICE_UUID, SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

MAC_ADDRESS = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def is_mira_valve(info: BluetoothServiceInfoBleak) -> bool:
    """Whether an advertisement looks like a supported (current generation) valve."""
    uuids = {uuid.lower() for uuid in info.service_uuids}
    if SERVICE_UUID in uuids:
        return True
    if LEGACY_SERVICE_UUID in uuids:
        return False
    return (info.name or "").lower().startswith("mira")


class MiraModeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Mira Mode valve."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: MiraConfigEntry) -> MiraModeOptionsFlow:
        """Outlet labels live in the options."""
        return MiraModeOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a valve seen advertising."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not is_mira_valve(discovery_info):
            return self.async_abort(reason="not_supported")
        self._address = discovery_info.address
        self._name = discovery_info.name
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before touching a discovered valve."""
        if user_input is not None:
            return await self.async_step_pair_method()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name or ""},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a discovered valve or type an address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if MAC_ADDRESS.match(address):
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                self._address = address
                self._name = self._discovered_names().get(address)
                return await self.async_step_pair_method()
            errors[CONF_ADDRESS] = "invalid_address"

        options = [
            SelectOptionDict(value=address, label=f"{name} ({address})")
            for address, name in self._discovered_names().items()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): SelectSelector(
                    SelectSelectorConfig(options=options, custom_value=True)
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pair an existing valve, e.g. after moving it to another proxy."""
        entry = self._get_reconfigure_entry()
        self._address = entry.data[CONF_ADDRESS]
        self._name = entry.title
        return await self.async_step_pair_method()

    async def async_step_pair_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bond now, or use a bond the adapter already has."""
        return self.async_show_menu(step_id="pair_method", menu_options=["pair", "already_paired"])

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Bond while the valve is in pairing mode."""
        return await self._async_connect_step("pair", user_input, pair=True)

    async def async_step_already_paired(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect over an existing bond."""
        return await self._async_connect_step("already_paired", user_input, pair=False)

    async def _async_connect_step(
        self, step_id: str, user_input: dict[str, Any] | None, pair: bool
    ) -> ConfigFlowResult:
        assert self._address is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await MiraClient(self.hass, self._address).identify(pair=pair)
            except ValveNotFound:
                errors["base"] = "not_found"
            except PairingFailed:
                errors["base"] = "pairing_failed"
            except ValveUnreachable:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error setting up %s", self._address)
                errors["base"] = "unknown"
            else:
                return self._async_finish(info)
        return self.async_show_form(
            step_id=step_id,
            errors=errors,
            description_placeholders={"address": self._address},
        )

    def _async_finish(self, info: ValveInfo) -> ConfigFlowResult:
        assert self._address is not None
        data = {CONF_ADDRESS: self._address, CONF_SERIAL: info.serial}
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=data
            )
        title = f"Mira {info.name}" if info.name else self._name or "Mira Mode"
        return self.async_create_entry(title=title, data=data, options=dict(DEFAULT_OUTLET_LABELS))

    @callback
    def _discovered_names(self) -> dict[str, str]:
        configured = self._async_current_ids(include_ignore=False)
        return {
            info.address: info.name
            for info in async_discovered_service_info(self.hass)
            if info.address not in configured and is_mira_valve(info)
        }


class MiraModeOptionsFlow(OptionsFlowWithReload):
    """Name the outlets; an empty label hides that outlet."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the outlet labels."""
        if user_input is not None:
            labels = {key: str(user_input.get(key, "")).strip() for key in CONF_OUTLET_LABELS}
            return self.async_create_entry(data=labels)

        current = {**DEFAULT_OUTLET_LABELS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(key, description={"suggested_value": current[key]}): TextSelector()
                for key in CONF_OUTLET_LABELS
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
