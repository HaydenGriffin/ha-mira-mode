"""Mira Mode valve protocol (current generation, GATT service ``267f0001-...``).

Pure functions only: no Bluetooth, no Home Assistant. Everything here is a
port of the reverse-engineered ``miramode`` library by Ryan Shaw
(https://github.com/ryan-shaw/mira-mode-control, MIT licence), which worked
out the framing, the opcodes and the state layout against real hardware.
See NOTICE in the repository root.

Every message, in both directions, is one frame::

    aa 55 <channel> <opcode> <length> <payload...> <checksum>

The checksum is the two's complement of the sum of the preceding bytes, so a
whole valid frame sums to zero modulo 256. Commands are written to
``COMMAND_CHAR_UUID``; replies arrive as notifications on ``EVENT_CHAR_UUID``,
one reply per command.
"""

from __future__ import annotations

from dataclasses import dataclass

SERVICE_UUID = "267f0001-eb15-43f5-94c3-67d2221188f7"
COMMAND_CHAR_UUID = "267f0002-eb15-43f5-94c3-67d2221188f7"
EVENT_CHAR_UUID = "267f0003-eb15-43f5-94c3-67d2221188f7"

#: Older Mira Mode valves advertise this instead and speak an unrelated,
#: CRC-16 based protocol (client id + slot pairing) that is not supported.
LEGACY_SERVICE_UUID = "bccb0001-ca66-11e5-88a4-0002a5d5c51b"

PREAMBLE = b"\xaa\x55"
HEADER_SIZE = 5  # preamble, channel, opcode, payload length
ENVELOPE_SIZE = HEADER_SIZE + 1  # plus the trailing checksum

OP_ACK = 0x01
OP_GET_STATE = 0x2B
OP_GET_SERIAL = 0x40
OP_GET_NAME = 0x44
OP_SET_OUTLETS = 0xAB

STATUS_OK = 0x01
STATUS_ERROR = 0x80

MAX_FLOW = 100
STATE_REQUEST = bytes([0x02])
SERIAL_REQUEST = bytes([0x01])
STATE_SIZE = 16

#: Outlet bits, as sent to SET_OUTLETS and reported in the state reply.
OUTLET_BITS = (1, 2, 4)
ALL_OUTLETS = 0b111

#: The target temperature is ten bits of tenths of a degree. The bits above
#: carry flags while the valve is running (the preset layout packs its
#: temperature the same way), so they must be masked off.
TARGET_TEMPERATURE_MASK = 0x3FF


@dataclass(frozen=True)
class Frame:
    """A decoded protocol message."""

    channel: int
    opcode: int
    payload: bytes


@dataclass(frozen=True)
class State:
    """What the valve is doing right now."""

    #: Bitfield of running outlets (bit 0 = outlet 1).
    outlets: int
    #: Measured water temperature in Celsius.
    temperature: float
    #: Temperature the valve is aiming for, or None when idle.
    target_temperature: float | None
    #: Flow percentage. The valve reports a resting default while idle,
    #: so this is only meaningful while running.
    flow: int
    #: The undecoded reply payload, kept for diagnostics.
    raw: bytes = b""

    @property
    def running(self) -> bool:
        """Whether any outlet is open."""
        return bool(self.outlets)

    def outlet_running(self, number: int) -> bool:
        """Whether outlet ``number`` (1-3) is open."""
        return bool(self.outlets & outlet_bit(number))


def outlet_bit(number: int) -> int:
    """Bit for outlet ``number`` (1-3)."""
    if not 1 <= number <= len(OUTLET_BITS):
        raise ValueError(f"outlet out of range: {number}")
    return OUTLET_BITS[number - 1]


def checksum(data: bytes) -> int:
    """Return the byte that makes ``data`` sum to zero modulo 256."""
    return -sum(data) & 0xFF


def encode_frame(opcode: int, payload: bytes = b"", channel: int = 0) -> bytes:
    """Build a complete frame, checksum included."""
    if not 0 <= channel <= 0xFF:
        raise ValueError(f"channel out of range: {channel}")
    if len(payload) > 0xFF:
        raise ValueError(f"payload too long: {len(payload)} bytes")
    body = PREAMBLE + bytes((channel, opcode, len(payload))) + payload
    return body + bytes((checksum(body),))


def decode_frame(raw: bytes) -> Frame | None:
    """Decode one complete frame, or return None if it doesn't check out."""
    if len(raw) < ENVELOPE_SIZE or not raw.startswith(PREAMBLE):
        return None
    if len(raw) != ENVELOPE_SIZE + raw[4] or sum(raw) & 0xFF:
        return None
    return Frame(channel=raw[2], opcode=raw[3], payload=raw[HEADER_SIZE:-1])


def encode_temperature(celsius: float) -> bytes:
    """Tenths of a degree, big endian. Zero means "leave it alone"."""
    tenths = round(celsius * 10)
    if not 0 <= tenths <= 0xFFFF:
        raise ValueError(f"temperature out of range: {celsius}")
    return tenths.to_bytes(2, "big")


def encode_outlets(outlets: int, temperature: float, flow: int) -> bytes:
    """Build a SET_OUTLETS payload.

    The valve runs exactly the outlets named and turns everything else off.
    A temperature of 0 leaves the set temperature alone (that is what a stop
    sends), and flow is forced to 0 when no outlet is named.
    """
    if not 0 <= outlets <= ALL_OUTLETS:
        raise ValueError(f"outlets out of range: {outlets}")
    if not 0 <= flow <= MAX_FLOW:
        raise ValueError(f"flow out of range: {flow}")
    return encode_temperature(temperature) + bytes((flow if outlets else 0, outlets))


def is_ack_ok(frame: Frame) -> bool:
    """Whether a reply acknowledges a command as accepted."""
    return bool(frame.payload) and frame.payload[0] == STATUS_OK


def decode_state(payload: bytes) -> State | None:
    """Decode a GET_STATE reply, or return None if it is too short.

    Offsets were confirmed upstream by commanding known values and reading
    them back: target at 10-11, flow at 12, outlet bits at 13, measured water
    temperature at 14-15 (tenths of a degree, big endian).
    """
    if len(payload) < STATE_SIZE:
        return None
    target = (int.from_bytes(payload[10:12], "big") & TARGET_TEMPERATURE_MASK) / 10
    return State(
        outlets=payload[13] & ALL_OUTLETS,
        temperature=int.from_bytes(payload[14:16], "big") / 10,
        target_temperature=target or None,
        flow=payload[12],
        raw=bytes(payload),
    )


def decode_text(raw: bytes) -> str | None:
    """Decode a NUL-padded ASCII field (name, serial), or None if it isn't one."""
    if any(byte and not 0x20 <= byte < 0x7F for byte in raw):
        return None
    return raw.split(b"\x00", 1)[0].decode("ascii").strip() or None


class FrameReader:
    """Reassembles frames from notification chunks.

    A reply usually arrives in a single notification, but nothing guarantees
    it, so bytes are buffered and frames taken out as they complete.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        """Drop anything buffered."""
        self._buffer.clear()

    def feed(self, chunk: bytes) -> list[Frame]:
        """Add a chunk and return every frame it completed."""
        self._buffer += chunk
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(PREAMBLE)
            if start < 0:
                # Keep a trailing byte: it may be the first half of a preamble.
                del self._buffer[: max(0, len(self._buffer) - 1)]
                break
            del self._buffer[:start]
            if len(self._buffer) < ENVELOPE_SIZE:
                break
            size = ENVELOPE_SIZE + self._buffer[4]
            if len(self._buffer) < size:
                break
            raw = bytes(self._buffer[:size])
            del self._buffer[:size]
            frame = decode_frame(raw)
            if frame is not None:
                frames.append(frame)
        return frames
