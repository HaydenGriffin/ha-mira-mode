"""Replies recorded from real valves.

Unless noted, these come from the upstream miramode test suite
(https://github.com/ryan-shaw/mira-mode-control, tests/captures.py, MIT),
recorded from a dual outlet shower and bath filler. Where a value was
commanded before being read back, the comment says so.
"""

from __future__ import annotations


def payload(text: str) -> bytes:
    """Bytes from spaced hex."""
    return bytes.fromhex(text.replace(" ", ""))


def frame(body_payload: bytes, opcode: int = 0x01, channel: int = 1) -> bytes:
    """Wrap a payload in the envelope a reply arrives in."""
    body = b"\xaa\x55" + bytes((channel, opcode, len(body_payload))) + body_payload
    return body + bytes((-sum(body) & 0xFF,))


#: The valve's reply to any command it accepted.
ACK = payload("aa 55 01 01 01 01 fd")

#: Nothing running. Flow rests at 16 (0x10) while idle.
STATE_IDLE = payload("21 20 00 00 00 00 00 00 80 00 00 00 10 00 01 00 00 01")

#: Outlet 1, four seconds after being commanded to 42.0 C at flow 50.
STATE_RUNNING = payload("21 20 00 00 00 12 00 00 00 00 01 a4 32 01 00 f6 12 01")

#: The same run six seconds later: water 24.6 C -> 26.0 C.
STATE_RUNNING_WARMER = payload("21 20 00 00 00 1b 00 00 00 00 01 a4 32 01 01 04 1b 01")

#: Just after stopping.
STATE_STOPPED = payload("21 20 00 00 00 00 00 00 80 00 00 00 10 00 01 03 00 01")

#: Opcode 0x44: the valve is named after where it is installed.
NAME = payload("42 61 74 68 72 6f 6f 6d 00 00 00 00 00 00 00 00")

#: Opcode 0x40, as printed on the unit.
SERIAL = payload("33 31 34 30 30 39 30 31 32 35 31 32 31 39 34 36 00 00")


def running_with_target_bytes(high: int, low: int) -> bytes:
    """STATE_RUNNING with different target temperature bytes (10-11).

    This integration's own field observation: while running at a 45.0 C
    target, a valve reported 05 c2 and 0d c2 there, which a plain 16 bit
    read turns into 147.4 C and 352.2 C. Masked to ten bits both are 0x1c2,
    i.e. 45.0 C.
    """
    raw = bytearray(STATE_RUNNING)
    raw[10], raw[11] = high, low
    return bytes(raw)


# The replies below were recorded by this integration's author from the Mira
# app talking to a current-generation dual-outlet valve. Values were checked
# against what the app showed at the time.

#: Outlet 1 just after a start: flow 48 commanded (byte 12), 30 reached so far
#: (byte 16), i.e. 7.5 L/min.
STATE_RAMPING = payload("21 c0 00 00 00 1e 00 00 00 00 01 c2 30 01 00 fd 1e 01")

#: Opcode 0x5B, idle. Bytes 1-2 (31.7) follow the water temperature, bytes 3-4
#: read 36.1 (the hot inlet guess) and bytes 26-27 are the cold inlet, 20.4 C.
INLETS = payload(
    "00 01 3d 01 69 ff ff 00 00 00 00 01 cc 30 00 03 01 00 04 99 00 00 03 0a 00 00 00 cc 00 00 0f"
)

#: Opcode 0x5C 02 00, while the app's last usage screen showed 10 s, 1 litre
#: and 44 C.
LAST_USAGE = payload("02 00 0a 00 01 58")

#: Opcode 0x5C 02 00 01: a different usage page, not decoded.
USAGE_PAGE = payload("00 01 01 00 08 00 57 55 83 ff")

#: Opcode 0x1B for outlet 1: 45.0 C maximum, flow 48 (12 L/min), 1800 s.
OUTLET_SETTINGS = payload("00 01 01 01 c2 01 2c 01 7c 40 10 30 07 08 00 00")

#: The same after setting 46.0 C, 14 L/min and 15 minutes in the app.
OUTLET_SETTINGS_CHANGED = payload("00 01 01 01 cc 01 2c 01 7c 58 10 38 03 84 00 00")
