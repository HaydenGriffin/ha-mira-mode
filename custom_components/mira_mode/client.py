"""Bluetooth transport for one Mira Mode valve.

Connections go through Home Assistant's Bluetooth stack, so a local adapter
or any connectable proxy (for example ESPHome) can carry them. The valve only
accepts one connection at a time and refuses an unbonded one as soon as
notifications are requested, which shapes everything below.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import RESPONSE_TIMEOUT
from .protocol import (
    COMMAND_CHAR_UUID,
    EVENT_CHAR_UUID,
    OP_GET_NAME,
    OP_GET_SERIAL,
    OP_GET_STATE,
    OP_SET_OUTLETS,
    SERIAL_REQUEST,
    STATE_REQUEST,
    Frame,
    FrameReader,
    State,
    decode_state,
    decode_text,
    encode_frame,
    encode_outlets,
    is_ack_ok,
)

_LOGGER = logging.getLogger(__name__)


class MiraError(Exception):
    """Base class for valve errors."""


class ValveNotFound(MiraError):
    """No connectable adapter or proxy can currently see the valve."""


class ValveUnreachable(MiraError):
    """Connecting or talking to the valve failed."""


class PairingFailed(MiraError):
    """The Bluetooth bond could not be created."""


class CommandRejected(MiraError):
    """The valve answered, but refused the command."""


@dataclass(frozen=True)
class ValveInfo:
    """What the valve says about itself while setting up."""

    name: str | None
    serial: str | None
    state: State


class MiraClient:
    """Talks to one valve; serialises access and owns the connection."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address
        self._client: BleakClientWithServiceCache | None = None
        self._reader = FrameReader()
        self._pending: asyncio.Future[Frame] | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Whether a connection is currently held open."""
        return self._client is not None and self._client.is_connected

    async def read_state(self) -> State:
        """Read the valve's state.

        The connection is kept open while the valve is running (it is read
        often then) and dropped otherwise, so the valve stays free for other
        clients such as the phone app.
        """
        async with self._lock:
            state = await self._guarded(self._read_state_unlocked)
            if not state.running:
                await self._disconnect()
            return state

    async def set_outlets(self, outlets: int, temperature: float, flow: int) -> State:
        """Run exactly ``outlets`` (0 stops everything); return the state after."""
        payload = encode_outlets(outlets, temperature, flow)

        async def command() -> State:
            ack = await self._request(OP_SET_OUTLETS, payload)
            if not is_ack_ok(ack):
                raise CommandRejected(ack.payload.hex(" ") or "empty reply")
            return await self._read_state_unlocked()

        async with self._lock:
            state = await self._guarded(command)
            if not state.running:
                await self._disconnect()
            return state

    async def identify(self, pair: bool) -> ValveInfo:
        """Connect (bonding first if ``pair``), then read name, serial and state.

        Used by the config flow to prove the link works end to end.
        """

        async def ask_text(opcode: int, payload: bytes = b"") -> str | None:
            try:
                return decode_text((await self._request(opcode, payload)).payload)
            except TimeoutError:
                return None

        async def run() -> ValveInfo:
            name = await ask_text(OP_GET_NAME)
            serial = await ask_text(OP_GET_SERIAL, SERIAL_REQUEST)
            return ValveInfo(name, serial, await self._read_state_unlocked())

        async with self._lock:
            await self._disconnect()
            try:
                await self._connect(pair=pair)
                return await self._guarded(run)
            finally:
                await self._disconnect()

    async def disconnect(self) -> None:
        """Drop the connection, if any."""
        async with self._lock:
            await self._disconnect()

    async def _guarded[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """Connect if needed and run ``operation``; translate BLE failures."""
        try:
            await self._connect()
            return await operation()
        except (BleakError, TimeoutError) as err:
            await self._disconnect()
            raise ValveUnreachable(str(err) or type(err).__name__) from err
        except MiraError:
            await self._disconnect()
            raise

    async def _read_state_unlocked(self) -> State:
        frame = await self._request(OP_GET_STATE, STATE_REQUEST)
        state = decode_state(frame.payload)
        if state is None:
            raise ValveUnreachable(f"unexpected state reply: {frame.payload.hex(' ')}")
        return state

    def _ble_device(self) -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            raise ValveNotFound(self.address)
        return device

    async def _connect(self, pair: bool = False) -> None:
        if self.connected:
            return
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device(),
                self.address,
                disconnected_callback=self._on_disconnect,
                max_attempts=2,
                ble_device_callback=self._ble_device,
            )
        except (BleakError, TimeoutError) as err:
            raise ValveUnreachable(str(err) or type(err).__name__) from err
        self._client = client
        try:
            if pair:
                await self._pair(client)
            self._reader.reset()
            await client.start_notify(EVENT_CHAR_UUID, self._on_notify)
        except (BleakError, TimeoutError) as err:
            await self._disconnect()
            raise ValveUnreachable(str(err) or type(err).__name__) from err

    async def _pair(self, client: BleakClientWithServiceCache) -> None:
        # The valve drops an unbonded link as soon as notifications are
        # requested, so the bond has to exist before subscribing.
        try:
            await client.pair()
        except (BleakError, NotImplementedError, TimeoutError) as err:
            await self._disconnect()
            raise PairingFailed(str(err) or type(err).__name__) from err

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        self._fail_pending()
        if client is None:
            return
        try:
            await client.disconnect()
        except BleakError:
            _LOGGER.debug("Ignoring error while disconnecting", exc_info=True)

    def _on_disconnect(self, client: object) -> None:
        if client is self._client:
            _LOGGER.debug("%s: valve disconnected", self.address)
            self._client = None
            self._fail_pending()

    def _fail_pending(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None and not pending.done():
            pending.set_exception(ValveUnreachable("disconnected while waiting"))

    def _on_notify(self, _char: object, data: bytearray) -> None:
        for frame in self._reader.feed(bytes(data)):
            pending, self._pending = self._pending, None
            if pending is not None and not pending.done():
                pending.set_result(frame)

    async def _request(self, opcode: int, payload: bytes = b"") -> Frame:
        if self._client is None:
            raise ValveUnreachable("not connected")
        future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending = future
        try:
            await self._client.write_gatt_char(
                COMMAND_CHAR_UUID, encode_frame(opcode, payload), response=False
            )
            return await asyncio.wait_for(future, RESPONSE_TIMEOUT)
        finally:
            if self._pending is future:
                self._pending = None
