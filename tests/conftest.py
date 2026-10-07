"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mira_mode.client import ValveInfo
from custom_components.mira_mode.const import CONF_SERIAL, DEFAULT_OUTLET_LABELS, DOMAIN
from custom_components.mira_mode.protocol import (
    LEGACY_SERVICE_UUID,
    SERVICE_UUID,
    State,
    decode_state,
)

from . import captures

ADDRESS = "AA:BB:CC:DD:EE:FF"
SERIAL = "3140090125121946"

IDLE = decode_state(captures.STATE_IDLE)
RUNNING = decode_state(captures.STATE_RUNNING)
assert IDLE is not None and RUNNING is not None


def service_info(
    name: str = "Mira 0000 Demo",
    address: str = ADDRESS,
    uuids: list[str] | None = None,
) -> BluetoothServiceInfoBleak:
    """An advertisement as Home Assistant's Bluetooth stack delivers it."""
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-70,
        manufacturer_data={},
        service_data={},
        service_uuids=[SERVICE_UUID] if uuids is None else uuids,
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=None,
        connectable=True,
        time=0,
        tx_power=None,
    )


MIRA_INFO = service_info()
LEGACY_INFO = service_info(name="Mira Old", uuids=[LEGACY_SERVICE_UUID])
UNNAMED_INFO = service_info(name="Mira 0001 Other", address="11:22:33:44:55:66", uuids=[])
OTHER_DEVICE_INFO = service_info(name="Kettle", address="11:22:33:44:55:77", uuids=[])


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
def bluetooth(enable_bluetooth: None) -> None:
    """Run every test with a mocked Bluetooth stack."""


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured valve."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mira Demo",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_SERIAL: SERIAL},
        options=dict(DEFAULT_OUTLET_LABELS),
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def valve() -> Generator[AsyncMock]:
    """Stand in for the valve: MiraClient's public methods, mocked."""
    with (
        patch(
            "custom_components.mira_mode.client.MiraClient.read_state",
            autospec=True,
        ) as read_state,
        patch(
            "custom_components.mira_mode.client.MiraClient.set_outlets",
            autospec=True,
        ) as set_outlets,
        patch(
            "custom_components.mira_mode.client.MiraClient.disconnect",
            autospec=True,
        ),
    ):
        mock = AsyncMock()
        mock.read_state = read_state
        mock.set_outlets = set_outlets
        read_state.return_value = IDLE

        async def follow(_self: object, outlets: int, temperature: float, flow: int) -> State:
            state = RUNNING if outlets else IDLE
            return State(
                outlets=outlets,
                temperature=state.temperature,
                target_temperature=temperature or None,
                flow=flow,
            )

        set_outlets.side_effect = follow
        yield mock


@pytest.fixture
def identify() -> Generator[AsyncMock]:
    """The config flow's link check."""
    with patch(
        "custom_components.mira_mode.config_flow.MiraClient.identify",
        return_value=ValveInfo(name="Demo", serial=SERIAL, state=IDLE),
    ) as mock:
        yield mock


@pytest.fixture
def no_setup() -> Generator[AsyncMock]:
    """Stop a finished flow from setting the entry up."""
    with patch("custom_components.mira_mode.async_setup_entry", return_value=True) as mock:
        yield mock
