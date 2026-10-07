"""Protocol: framing, checksums, encoding and decoding against real captures."""

from __future__ import annotations

import pytest

from custom_components.mira_mode import protocol as p

from . import captures


def test_checksum_makes_a_frame_sum_to_zero() -> None:
    frame = p.encode_frame(p.OP_GET_STATE, p.STATE_REQUEST)
    assert sum(frame) % 256 == 0


@pytest.mark.parametrize(
    ("opcode", "payload", "expected"),
    [
        # Each of these was accepted by a real valve (upstream test vectors).
        (p.OP_SET_OUTLETS, b"\x01\x86\x64\x01", "aa5500ab040186640166"),  # outlet 1, 39.0 C
        (p.OP_SET_OUTLETS, b"\x01\x86\x00\x00", "aa5500ab0401860000cb"),  # stop
        (p.OP_SET_OUTLETS, b"\x00\x00\x00\x00", "aa5500ab040000000052"),  # stop, keep temp
        (0xB1, b"\x02", "aa5500b101024d"),  # run preset 2
        (p.OP_GET_STATE, b"\x02", "aa55002b0102d3"),  # read state
    ],
)
def test_encodes_frames_the_valve_accepted(opcode: int, payload: bytes, expected: str) -> None:
    assert p.encode_frame(opcode, payload).hex() == expected


@pytest.mark.parametrize(
    ("outlets", "temperature", "flow", "expected"),
    [
        (1, 39.0, 100, "aa5500ab040186640166"),
        (0, 0.0, 0, "aa5500ab040000000052"),
        # Flow is forced to zero when nothing runs, whatever was asked.
        (0, 0.0, 80, "aa5500ab040000000052"),
    ],
)
def test_encode_outlets_builds_the_accepted_frames(
    outlets: int, temperature: float, flow: int, expected: str
) -> None:
    payload = p.encode_outlets(outlets, temperature, flow)
    assert p.encode_frame(p.OP_SET_OUTLETS, payload).hex() == expected


@pytest.mark.parametrize(
    ("outlets", "temperature", "flow"),
    [(1, -1.0, 50), (1, 7000.0, 50), (1, 40.0, 101), (1, 40.0, -1), (8, 40.0, 50)],
)
def test_encode_outlets_rejects_out_of_range(outlets: int, temperature: float, flow: int) -> None:
    with pytest.raises(ValueError):
        p.encode_outlets(outlets, temperature, flow)


@pytest.mark.parametrize(
    ("celsius", "expected"), [(39.0, "0186"), (42.0, "01a4"), (0.0, "0000"), (39.55, "018c")]
)
def test_encodes_temperature_in_tenths(celsius: float, expected: str) -> None:
    assert p.encode_temperature(celsius).hex() == expected


def test_encode_frame_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        p.encode_frame(p.OP_ACK, b"", channel=256)
    with pytest.raises(ValueError):
        p.encode_frame(p.OP_ACK, b"\x00" * 256)


def test_decodes_an_ack() -> None:
    frame = p.decode_frame(captures.ACK)
    assert frame == p.Frame(channel=1, opcode=p.OP_ACK, payload=b"\x01")
    assert p.is_ack_ok(frame)


@pytest.mark.parametrize("payload", [b"", b"\x80", b"\x00"])
def test_anything_but_status_ok_is_not_an_ack(payload: bytes) -> None:
    assert not p.is_ack_ok(p.Frame(channel=1, opcode=p.OP_ACK, payload=payload))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xaa",
        b"\xaa\x55",
        b"\xaa\x55\x00\xb1\x01",
        b"\x00\x55\x00\xb1\x01\x02\x4c",
    ],
    ids=["empty", "one byte", "preamble only", "truncated", "bad preamble"],
)
def test_rejects_malformed_frames(raw: bytes) -> None:
    assert p.decode_frame(raw) is None


def test_rejects_a_corrupted_checksum() -> None:
    frame = bytearray(captures.ACK)
    frame[-1] ^= 0xFF
    assert p.decode_frame(bytes(frame)) is None


def test_rejects_a_length_that_disagrees_with_the_frame() -> None:
    frame = bytearray(p.encode_frame(0xB1, b"\x02"))
    frame[4] = 9
    assert p.decode_frame(bytes(frame)) is None


class TestState:
    def test_idle(self) -> None:
        state = p.decode_state(captures.STATE_IDLE)
        assert state is not None
        assert not state.running
        assert state.outlets == 0
        assert state.target_temperature is None
        assert state.temperature == 25.6
        assert state.raw == captures.STATE_IDLE

    def test_running_shows_what_was_commanded(self) -> None:
        state = p.decode_state(captures.STATE_RUNNING)
        assert state is not None
        assert state.running
        assert state.outlet_running(1)
        assert not state.outlet_running(2)
        assert state.target_temperature == 42.0
        assert state.flow == 50
        assert state.temperature == 24.6

    def test_follows_the_water_warming(self) -> None:
        state = p.decode_state(captures.STATE_RUNNING_WARMER)
        assert state is not None
        assert state.temperature == 26.0
        assert state.target_temperature == 42.0

    def test_stopped(self) -> None:
        state = p.decode_state(captures.STATE_STOPPED)
        assert state is not None
        assert not state.running
        assert state.target_temperature is None

    @pytest.mark.parametrize("high", [0x05, 0x0D, 0x01])
    def test_masks_flag_bits_above_the_target(self, high: int) -> None:
        state = p.decode_state(captures.running_with_target_bytes(high, 0xC2))
        assert state is not None
        assert state.target_temperature == 45.0

    def test_ignores_bits_above_the_outlets(self) -> None:
        raw = bytearray(captures.STATE_RUNNING)
        raw[13] = 0b1000001  # bit 6 means paused upstream; not an outlet
        state = p.decode_state(bytes(raw))
        assert state is not None
        assert state.outlets == 1

    def test_reports_every_outlet(self) -> None:
        raw = bytearray(captures.STATE_RUNNING)
        raw[13] = 0b111
        state = p.decode_state(bytes(raw))
        assert state is not None
        assert all(state.outlet_running(n) for n in (1, 2, 3))

    def test_rejects_a_short_reply(self) -> None:
        assert p.decode_state(captures.STATE_IDLE[:8]) is None


@pytest.mark.parametrize("number", [0, 4])
def test_outlet_bit_rejects_unknown_outlets(number: int) -> None:
    with pytest.raises(ValueError):
        p.outlet_bit(number)


class TestText:
    def test_name(self) -> None:
        assert p.decode_text(captures.NAME) == "Bathroom"

    def test_serial(self) -> None:
        assert p.decode_text(captures.SERIAL) == "3140090125121946"

    @pytest.mark.parametrize("raw", [b"\x00" * 16, b"\x03\x9f\x00\xe2"])
    def test_rejects_non_text(self, raw: bytes) -> None:
        assert p.decode_text(raw) is None


class TestFrameReader:
    def test_whole_frame(self) -> None:
        assert len(p.FrameReader().feed(captures.ACK)) == 1

    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7])
    def test_reassembles_fragments(self, chunk_size: int) -> None:
        raw = captures.frame(captures.STATE_RUNNING)
        reader = p.FrameReader()
        frames: list[p.Frame] = []
        for i in range(0, len(raw), chunk_size):
            frames += reader.feed(raw[i : i + chunk_size])
        assert [f.payload for f in frames] == [captures.STATE_RUNNING]

    def test_two_frames_together(self) -> None:
        frames = p.FrameReader().feed(captures.ACK + captures.ACK)
        assert [f.payload for f in frames] == [b"\x01", b"\x01"]

    def test_skips_leading_junk(self) -> None:
        assert len(p.FrameReader().feed(b"\x00\x13\x99" + captures.ACK)) == 1

    def test_preamble_split_across_chunks(self) -> None:
        reader = p.FrameReader()
        assert reader.feed(captures.ACK[:1]) == []
        assert len(reader.feed(captures.ACK[1:])) == 1

    def test_recovers_after_a_corrupt_frame(self) -> None:
        corrupt = bytearray(captures.ACK)
        corrupt[-1] ^= 0xFF
        reader = p.FrameReader()
        assert reader.feed(bytes(corrupt)) == []
        assert len(reader.feed(captures.ACK)) == 1

    def test_reset_drops_buffered_bytes(self) -> None:
        reader = p.FrameReader()
        reader.feed(captures.ACK[:3])
        reader.reset()
        assert reader.feed(captures.ACK[3:]) == []

    def test_does_not_grow_on_junk(self) -> None:
        reader = p.FrameReader()
        for _ in range(100):
            assert reader.feed(b"\x11\x22\x33\x44") == []
        assert len(reader._buffer) <= 4
