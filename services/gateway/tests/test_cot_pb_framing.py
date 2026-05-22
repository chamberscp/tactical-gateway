"""Tests for protobuf framing and route engine logic (no real network)."""

from __future__ import annotations

import pytest

from services.gateway.normalizers.cot_pb import (
    CoTPbError,
    encode_frame,
    try_decode_frame,
)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 1, 127, 128, 256, 16384])
def test_frame_roundtrip(size):
    payload = bytes(range(256)) * (size // 256) + bytes(size % 256)
    payload = payload[:size]
    framed = encode_frame(payload)
    buf = bytearray(framed)
    out, consumed = try_decode_frame(buf)
    assert out == payload
    assert consumed == len(framed)


def test_frame_partial_returns_none():
    payload = b"hello world"
    framed = encode_frame(payload)
    # Feed all but the last byte.
    buf = bytearray(framed[:-1])
    out, consumed = try_decode_frame(buf)
    assert out is None
    assert consumed == 0


def test_frame_concatenated_two():
    a = encode_frame(b"first")
    b = encode_frame(b"second")
    buf = bytearray(a + b)
    out1, c1 = try_decode_frame(buf)
    del buf[:c1]
    out2, c2 = try_decode_frame(buf)
    assert out1 == b"first"
    assert out2 == b"second"


def test_frame_bad_first_magic_raises():
    buf = bytearray(b"\xaa\x05\xbf12345")
    with pytest.raises(CoTPbError):
        try_decode_frame(buf)


def test_frame_bad_second_magic_raises():
    # Build a frame with the wrong second magic byte.
    buf = bytearray(b"\xbf\x05\xaa12345")
    with pytest.raises(CoTPbError):
        try_decode_frame(buf)
