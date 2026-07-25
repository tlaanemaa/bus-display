"""Compact, validated state kept in ESP32 RTC user memory.

Only semantic screen content and slow-cadence timestamps are retained.  The
48 KB framebuffer is deliberately never persisted: after deep sleep the old
semantic frame is rendered into the one framebuffer and streamed as the
differential update's 0x10 plane.

This module is pure Python so the encoding can be tested on the host.  The
caller owns machine.RTC().memory().
"""
import json

if False:
    from typing import Any
    from models import RetainedState, Settings

MAGIC = b"BD1:"
MAX_BYTES = 2048  # ESP32 MicroPython RTC user-memory limit
RETAINED_VERSION = 2
RENDER_REVISION = 1

_READING_KEYS = {"date", "condition", "tmin", "tmax", "precip"}
_CONDITIONS = {
    "clear", "partly", "cloudy", "fog", "drizzle", "rain",
    "rain_heavy", "snow", "thunder",
}
_SECTION_KEYS = {
    "stop_key", "name", "hero_main", "hero_unit", "badge_line", "dest",
    "rows", "stale",
}
_FRAME_KEYS = {"sections", "footer", "status"}
_STATE_KEYS = {
    "v", "render_rev", "settings", "frame", "last_full", "weather",
    "weather_time", "last_ntp",
}


def _checksum(data: bytes) -> int:
    # Small allocation-free checksum. This is corruption detection, not
    # authentication; versioning handles incompatible firmware layouts.
    value = 1
    for byte in data:
        value = (value * 33 + byte) & 0xFFFFFFFF
    return value


def settings_fingerprint(cfg: "Settings") -> str:
    """Return the render-compatibility token for normalized settings."""
    weather = cfg["weather"]
    normalized_weather = None
    if weather is not None:
        normalized_weather = [
            weather["enabled"],
            weather["latitude"],
            weather["longitude"],
            weather["pull_interval_min"],
            weather["max_age_min"],
        ]
    normalized = [
        [[stop["name"], stop["site_id"]] for stop in cfg["stops"]],
        cfg["direction_code"],
        cfg["departures_per_stop"],
        cfg["full_refresh_interval_min"],
        normalized_weather,
    ]
    canonical = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    return "%08x" % _checksum(canonical)


def _is_int_or_none(value: "Any") -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _valid_reading(value: "Any") -> bool:
    if not isinstance(value, dict) or set(value) != _READING_KEYS:
        return False
    precip = value["precip"]
    return (
        isinstance(value["date"], str)
        and value["condition"] in _CONDITIONS
        and isinstance(value["tmin"], int)
        and not isinstance(value["tmin"], bool)
        and isinstance(value["tmax"], int)
        and not isinstance(value["tmax"], bool)
        and _is_int_or_none(precip)
        and (precip is None or 0 <= precip <= 100)
    )


def _valid_status(value: "Any") -> bool:
    if not isinstance(value, dict):
        return False
    kind = value.get("kind")
    if kind == "weather":
        return set(value) == {"kind", "reading"} and _valid_reading(value["reading"])
    return kind in ("none", "wifi_error", "weather_error") and set(value) == {"kind"}


def _valid_section(value: "Any") -> bool:
    if not isinstance(value, dict) or set(value) != _SECTION_KEYS:
        return False
    if not (
        isinstance(value["stop_key"], str)
        and isinstance(value["name"], str)
        and isinstance(value["dest"], str)
        and isinstance(value["stale"], bool)
    ):
        return False
    for key in ("hero_main", "hero_unit", "badge_line"):
        if value[key] is not None and not isinstance(value[key], str):
            return False
    rows = value["rows"]
    if not isinstance(rows, list):
        return False
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 3
            or not all(isinstance(item, str) for item in row)
        ):
            return False
    return True


def _valid_frame(value: "Any") -> bool:
    if not isinstance(value, dict) or set(value) != _FRAME_KEYS:
        return False
    sections = value["sections"]
    footer = value["footer"]
    return (
        isinstance(sections, list)
        and all(_valid_section(section) for section in sections)
        and isinstance(footer, list)
        and all(isinstance(line, str) for line in footer)
        and _valid_status(value["status"])
    )


def _valid_state(value: "Any") -> bool:
    if set(value) != _STATE_KEYS:
        return False
    reading = value["weather"]
    return (
        isinstance(value["v"], int)
        and not isinstance(value["v"], bool)
        and isinstance(value["render_rev"], int)
        and not isinstance(value["render_rev"], bool)
        and isinstance(value["settings"], str)
        and _valid_frame(value["frame"])
        and (reading is None or _valid_reading(reading))
        and _is_int_or_none(value["last_full"])
        and _is_int_or_none(value["weather_time"])
        and _is_int_or_none(value["last_ntp"])
    )


def encode(state: "RetainedState") -> bytes:
    body = json.dumps(state, separators=(",", ":")).encode("utf-8")
    raw = MAGIC + ("%08x:" % _checksum(body)).encode("ascii") + body
    if len(raw) > MAX_BYTES:
        raise ValueError("retained state is %d bytes; RTC limit is %d" % (len(raw), MAX_BYTES))
    return raw


def decode(
    raw: bytes,
    expected_fingerprint: str,
    expected_stop_keys: "list[str]",
) -> "RetainedState | None":
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
        if not isinstance(state, dict):
            return None
        if state.get("v") != RETAINED_VERSION:
            return None
        if state.get("render_rev") != RENDER_REVISION:
            return None
        if state.get("settings") != expected_fingerprint:
            return None
        if not _valid_state(state):
            return None
        sections = state["frame"]["sections"]
        if [section["stop_key"] for section in sections] != expected_stop_keys:
            return None
        return state  # type: ignore[return-value]
    except (ValueError, TypeError, UnicodeError):
        return None
