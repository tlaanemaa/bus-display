"""Compact, validated state kept in ESP32 RTC user memory.

Only semantic screen content and slow-cadence timestamps are retained.  The
48 KB framebuffer is deliberately never persisted: after deep sleep the old
semantic frame is rendered into the one framebuffer and streamed as the
differential update's 0x10 plane.

This module is pure Python so the encoding can be tested on the host.  The
caller owns machine.RTC().memory().
"""
import json

MAGIC = b"BD1:"
MAX_BYTES = 2048  # ESP32 MicroPython RTC user-memory limit


def _checksum(data):
    # Small allocation-free checksum. This is corruption detection, not
    # authentication; versioning handles incompatible firmware layouts.
    value = 1
    for byte in data:
        value = (value * 33 + byte) & 0xFFFFFFFF
    return value


def encode(state):
    body = json.dumps(state).encode("utf-8")
    raw = MAGIC + ("%08x:" % _checksum(body)).encode("ascii") + body
    if len(raw) > MAX_BYTES:
        raise ValueError("retained state is %d bytes; RTC limit is %d" % (len(raw), MAX_BYTES))
    return raw


def decode(raw):
    try:
        if not raw or not raw.startswith(MAGIC):
            return None
        split = len(MAGIC) + 8
        if len(raw) <= split or raw[split:split + 1] != b":":
            return None
        expected = int(raw[len(MAGIC):split], 16)
        body = raw[split + 1:]
        if _checksum(body) != expected:
            return None
        state = json.loads(body.decode("utf-8"))
        if not isinstance(state, dict) or state.get("v") != 1:
            return None
        return state
    except (ValueError, TypeError, UnicodeError):
        return None
