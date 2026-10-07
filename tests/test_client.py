"""The Bluetooth transport, against a stand-in valve that answers frames."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
import pytest

from custom_components.mira_mode.client import (
    CommandRejected,
    MiraClient,
    PairingFailed,
    ValveNotFound,
    ValveUnreachable,
)
from custom_components.mira_mode.protocol import (
    OP_GET_NAME,
    OP_GET_SERIAL,
    OP_GET_STATE,
    OP_SET_OUTLETS,
    decode_frame,
)

from . import captures
from .conftest import ADDRESS

NACK = captures.frame(b"\x80")


class FakeValve:
    """A BleakClient stand-in: answers each written frame with a canned reply."""

    def __init__(self, replies: dict[int, bytes], chunk: int = 0) -> None:
        self.replies = replies
        self.chunk = chunk
        self.is_connected = True
        self.log: list[Any] = []
        self.written: list[bytes] = []
        self.pair_error: Exception | None = None
        self._notify: Callable[[object, bytearray], None] | None = None

    async def pair(self) -> bool:
        self.log.append("pair")
        if self.pair_error:
            raise self.pair_error
        return True

    async def start_notify(self, _uuid: str, callback: Callable[[object, bytearray], None]) -> None:
        self.log.append("notify")
        self._notify = callback

    async def write_gatt_char(self, _uuid: str, data: bytes, response: bool) -> None:
        self.written.append(bytes(data))
        frame = decode_frame(bytes(data))
        assert frame is not None
        self.log.append(frame.opcode)
        reply = self.replies.get(frame.opcode)
        if reply is None:
            return
        size = self.chunk or len(reply)
        loop = asyncio.get_running_loop()
        for i in range(0, len(reply), size):
            loop.call_soon(self._notify, None, bytearray(reply[i : i + size]))

    async def disconnect(self) -> None:
        self.log.append("disconnect")
        self.is_connected = False


def replies(state: bytes = captures.STATE_IDLE, ack: bytes = captures.ACK) -> dict[int, bytes]:
    return {
        OP_GET_STATE: captures.frame(state),
        OP_SET_OUTLETS: ack,
        OP_GET_NAME: captures.frame(captures.NAME),
        OP_GET_SERIAL: captures.frame(captures.SERIAL),
    }


@pytest.fixture
def connect() -> Generator[AsyncMock]:
    with (
        patch(
            "custom_components.mira_mode.client.bluetooth.async_ble_device_from_address",
            return_value=BLEDevice(ADDRESS, "Mira", {}),
        ),
        patch("custom_components.mira_mode.client.establish_connection") as establish,
    ):
        yield establish


async def test_idle_read_disconnects_afterwards(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve(replies())
    connect.return_value = valve
    state = await MiraClient(hass, ADDRESS).read_state()
    assert not state.running
    assert valve.log == ["notify", OP_GET_STATE, "disconnect"]
    assert valve.written == [bytes.fromhex("aa55002b0102d3")]


async def test_running_read_keeps_the_connection(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve(replies(captures.STATE_RUNNING))
    connect.return_value = valve
    client = MiraClient(hass, ADDRESS)
    await client.read_state()
    await client.read_state()
    assert client.connected
    assert connect.await_count == 1
    assert "disconnect" not in valve.log


async def test_fragmented_replies_are_reassembled(hass: HomeAssistant, connect: AsyncMock) -> None:
    connect.return_value = FakeValve(replies(captures.STATE_RUNNING), chunk=3)
    state = await MiraClient(hass, ADDRESS).read_state()
    assert state.target_temperature == 42.0


async def test_set_outlets_sends_the_frame_and_reads_back(
    hass: HomeAssistant, connect: AsyncMock
) -> None:
    valve = FakeValve(replies(captures.STATE_RUNNING))
    connect.return_value = valve
    state = await MiraClient(hass, ADDRESS).set_outlets(1, 39.0, 100)
    assert state.running
    assert valve.written[0] == bytes.fromhex("aa5500ab040186640166")
    assert valve.log == ["notify", OP_SET_OUTLETS, OP_GET_STATE]


async def test_stop_disconnects_once_idle(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve(replies(captures.STATE_STOPPED))
    connect.return_value = valve
    await MiraClient(hass, ADDRESS).set_outlets(0, 0.0, 0)
    assert valve.written[0] == bytes.fromhex("aa5500ab040000000052")
    assert valve.log[-1] == "disconnect"


async def test_rejected_command(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve(replies(ack=NACK))
    connect.return_value = valve
    with pytest.raises(CommandRejected):
        await MiraClient(hass, ADDRESS).set_outlets(1, 39.0, 100)
    assert valve.log[-1] == "disconnect"


async def test_silence_is_a_failure(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve({})
    connect.return_value = valve
    with (
        patch("custom_components.mira_mode.client.RESPONSE_TIMEOUT", 0.01),
        pytest.raises(ValveUnreachable),
    ):
        await MiraClient(hass, ADDRESS).set_outlets(1, 39.0, 100)
    assert valve.log[-1] == "disconnect"


async def test_connect_failure(hass: HomeAssistant, connect: AsyncMock) -> None:
    connect.side_effect = BleakError("GATT error 133")
    with pytest.raises(ValveUnreachable, match="133"):
        await MiraClient(hass, ADDRESS).read_state()


async def test_out_of_range(hass: HomeAssistant, connect: AsyncMock) -> None:
    with (
        patch(
            "custom_components.mira_mode.client.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        pytest.raises(ValveNotFound),
    ):
        await MiraClient(hass, ADDRESS).read_state()
    connect.assert_not_awaited()


async def test_identify_bonds_before_subscribing(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve(replies())
    connect.return_value = valve
    info = await MiraClient(hass, ADDRESS).identify(pair=True)
    assert (info.name, info.serial, info.state.running) == ("Bathroom", "3140090125121946", False)
    assert valve.log[:2] == ["pair", "notify"]
    assert valve.log[-1] == "disconnect"


async def test_identify_without_pairing(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve(replies())
    connect.return_value = valve
    await MiraClient(hass, ADDRESS).identify(pair=False)
    assert "pair" not in valve.log


async def test_identify_tolerates_unanswered_name_and_serial(
    hass: HomeAssistant, connect: AsyncMock
) -> None:
    connect.return_value = FakeValve({OP_GET_STATE: captures.frame(captures.STATE_IDLE)})
    with patch("custom_components.mira_mode.client.RESPONSE_TIMEOUT", 0.01):
        info = await MiraClient(hass, ADDRESS).identify(pair=False)
    assert info.name is None and info.serial is None


@pytest.mark.parametrize("error", [BleakError("Pairing failed: 129"), NotImplementedError()])
async def test_pairing_failure(hass: HomeAssistant, connect: AsyncMock, error: Exception) -> None:
    valve = FakeValve(replies())
    valve.pair_error = error
    connect.return_value = valve
    with pytest.raises(PairingFailed):
        await MiraClient(hass, ADDRESS).identify(pair=True)
    assert "notify" not in valve.log
    assert valve.log[-1] == "disconnect"


async def test_disconnect_while_waiting_fails_fast(hass: HomeAssistant, connect: AsyncMock) -> None:
    valve = FakeValve({})
    connect.return_value = valve
    client = MiraClient(hass, ADDRESS)
    task = asyncio.create_task(client.read_state())
    while OP_GET_STATE not in valve.log:
        await asyncio.sleep(0)
    on_disconnect = connect.call_args.kwargs["disconnected_callback"]
    on_disconnect(valve)
    with pytest.raises(ValveUnreachable, match="disconnected"):
        await task
