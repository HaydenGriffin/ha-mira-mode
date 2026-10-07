"""Config flow: discovery, manual entry, pairing, reconfigure and options."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mira_mode.client import PairingFailed, ValveNotFound, ValveUnreachable
from custom_components.mira_mode.const import CONF_SERIAL, DEFAULT_OUTLET_LABELS, DOMAIN

from .conftest import (
    ADDRESS,
    LEGACY_INFO,
    MIRA_INFO,
    OTHER_DEVICE_INFO,
    SERIAL,
    UNNAMED_INFO,
)

DISCOVERED = "custom_components.mira_mode.config_flow.async_discovered_service_info"


async def _start_user_flow(hass: HomeAssistant, discovered: list) -> dict:
    with patch(DISCOVERED, return_value=discovered):
        return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_user_flow_pairs_a_discovered_valve(
    hass: HomeAssistant, identify: AsyncMock, no_setup: AsyncMock
) -> None:
    result = await _start_user_flow(hass, [MIRA_INFO, LEGACY_INFO, OTHER_DEVICE_INFO])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    options = result["data_schema"].schema[CONF_ADDRESS].config["options"]
    assert [o["value"] for o in options] == [ADDRESS]

    with patch(DISCOVERED, return_value=[MIRA_INFO]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pair_method"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mira Demo"
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_SERIAL: SERIAL}
    assert result["options"] == DEFAULT_OUTLET_LABELS
    assert result["result"].unique_id == ADDRESS
    identify.assert_awaited_once_with(pair=True)
    no_setup.assert_awaited_once()


async def test_user_flow_accepts_a_typed_address(
    hass: HomeAssistant, identify: AsyncMock, no_setup: AsyncMock
) -> None:
    result = await _start_user_flow(hass, [])
    with patch(DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: " aa:bb:cc:dd:ee:ff "}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "already_paired"}
    )
    assert result["step_id"] == "already_paired"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS
    identify.assert_awaited_once_with(pair=False)


async def test_user_flow_rejects_a_malformed_address(hass: HomeAssistant) -> None:
    result = await _start_user_flow(hass, [])
    with patch(DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: "not-an-address"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


async def test_unnamed_service_but_mira_name_is_listed(hass: HomeAssistant) -> None:
    result = await _start_user_flow(hass, [UNNAMED_INFO])
    options = result["data_schema"].schema[CONF_ADDRESS].config["options"]
    assert [o["value"] for o in options] == [UNNAMED_INFO.address]


async def test_bluetooth_discovery(
    hass: HomeAssistant, identify: AsyncMock, no_setup: AsyncMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=MIRA_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mira Demo"


async def test_bluetooth_discovery_without_a_valve_name_uses_the_advert(
    hass: HomeAssistant, identify: AsyncMock, no_setup: AsyncMock
) -> None:
    identify.return_value = identify.return_value.__class__(
        name=None, serial=None, state=identify.return_value.state
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=MIRA_INFO
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["title"] == MIRA_INFO.name
    assert result["data"][CONF_SERIAL] is None


async def test_bluetooth_discovery_of_an_older_valve_aborts(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=LEGACY_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_already_configured(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=MIRA_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    result = await _start_user_flow(hass, [MIRA_INFO])
    assert result["data_schema"].schema[CONF_ADDRESS].config["options"] == []
    with patch(DISCOVERED, return_value=[MIRA_INFO]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ValveNotFound("x"), "not_found"),
        (PairingFailed("x"), "pairing_failed"),
        (ValveUnreachable("x"), "cannot_connect"),
        (RuntimeError("x"), "unknown"),
    ],
)
async def test_pairing_errors_can_be_retried(
    hass: HomeAssistant,
    identify: AsyncMock,
    no_setup: AsyncMock,
    error: Exception,
    reason: str,
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=MIRA_INFO
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    valve_info = identify.return_value
    identify.side_effect = error
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": reason}

    identify.side_effect = None
    identify.return_value = valve_info
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_pairs_again(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    identify: AsyncMock,
    no_setup: AsyncMock,
) -> None:
    identify.return_value = identify.return_value.__class__(
        name="Demo", serial="999", state=identify.return_value.state
    )
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {CONF_ADDRESS: ADDRESS, CONF_SERIAL: "999"}


async def test_options_flow_sets_outlet_labels(
    hass: HomeAssistant, config_entry: MockConfigEntry, no_setup: AsyncMock
) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"outlet_1": " Bath ", "outlet_2": "Shower"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {"outlet_1": "Bath", "outlet_2": "Shower", "outlet_3": ""}
