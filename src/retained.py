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

MAGIC = b"BD1:"
MAX_BYTES = 2048  # ESP32 MicroPython RTC user-memory limit
STATE_VERSION = 2
RENDER_REVISION = 1

_STATE_KEYS = (
    "v", "render_rev", "settings", "frame", "last_full", "weather",
    "weather_time", "weather_bucket", "last_ntp",
)
_SECTION_KEYS = (
    "name", "hero_main", "hero_unit", "badge_line", "dest", "rows", "stale",
)
_WEATHER_KEYS = ("date", "condition", "tmin", "tmax", "precip")
_WEATHER_CONDITIONS = (
    "clear", "partly", "cloudy", "fog", "drizzle", "rain", "rain_heavy",
    "snow", "thunder",
)
_STATUS_WIFI_ERROR = "wifi_error"
_STATUS_WEATHER_ERROR = "error"


def _checksum(data: bytes) -> int:
    # Small allocation-free checksum. This is corruption detection, not
    # authentication; versioning handles incompatible firmware layouts.
    value = 1
    for byte in data:
        value = (value * 33 + byte) & 0xFFFFFFFF
    return value


def settings_fingerprint(cfg: "dict[str, Any]") -> str:
    """Stable identity for settings that affect retained frame rendering."""
    stops = [[stop["name"], stop["site_id"]] for stop in cfg["stops"]]
    weather_identity = None
    weather = cfg.get("weather")
    if (isinstance(weather, dict) and weather.get("enabled", True)
            and weather.get("latitude") is not None
            and weather.get("longitude") is not None):
        weather_identity = [weather["latitude"], weather["longitude"]]
    identity = [
        stops,
        cfg.get("direction_code", 2),
        cfg.get("departures_per_stop", 3),
        weather_identity,
    ]
    canonical = json.dumps(identity, separators=(",", ":")).encode("utf-8")
    return "%08x" % _checksum(canonical)


def _has_exact_keys(value: "dict[str, Any]", keys: "tuple[str, ...]") -> bool:
    if len(value) != len(keys):
        return False
    for key in keys:
        if key not in value:
            return False
    return True


def _is_integer(value: "Any") -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_weather(value: "Any") -> bool:
    if not isinstance(value, dict) or not _has_exact_keys(value, _WEATHER_KEYS):
        return False
    date = value["date"]
    if date is not None and not isinstance(date, str):
        return False
    if value["condition"] not in _WEATHER_CONDITIONS:
        return False
    if not _is_integer(value["tmin"]) or not _is_integer(value["tmax"]):
        return False
    precip = value["precip"]
    return precip is None or (_is_integer(precip) and 0 <= precip <= 100)


def _valid_status(value: "Any") -> bool:
    return (value is None or value == _STATUS_WIFI_ERROR
            or value == _STATUS_WEATHER_ERROR or _valid_weather(value))


def _valid_section(value: "Any") -> bool:
    if not isinstance(value, dict) or not _has_exact_keys(value, _SECTION_KEYS):
        return False
    if not isinstance(value["name"], str) or not isinstance(value["dest"], str):
        return False
    if not isinstance(value["stale"], bool) or not isinstance(value["rows"], list):
        return False

    hero_main = value["hero_main"]
    if hero_main is None:
        if (value["hero_unit"] is not None or value["badge_line"] is not None
                or value["dest"] != "No departures" or value["rows"]):
            return False
    else:
        if not isinstance(hero_main, str) or not isinstance(value["badge_line"], str):
            return False
        if value["hero_unit"] is not None and not isinstance(value["hero_unit"], str):
            return False

    for row in value["rows"]:
        if (not isinstance(row, list) or len(row) != 3
                or not all(isinstance(item, str) for item in row)):
            return False
    return True


def _valid_state(
    state: "Any", expected_fingerprint: str, expected_sections: int,
) -> bool:
    if not isinstance(state, dict) or not _has_exact_keys(state, _STATE_KEYS):
        return False
    if (not _is_integer(state["v"]) or not _is_integer(state["render_rev"])
            or state["v"] != STATE_VERSION or state["render_rev"] != RENDER_REVISION
            or state["settings"] != expected_fingerprint):
        return False

    frame = state["frame"]
    if not isinstance(frame, list) or len(frame) != 3:
        return False
    sections, footer, status = frame
    if (not isinstance(sections, list) or len(sections) != expected_sections
            or not all(_valid_section(section) for section in sections)):
        return False
    if (not isinstance(footer, list) or not 1 <= len(footer) <= 2
            or not all(isinstance(line, str) for line in footer)):
        return False
    if not _valid_status(status):
        return False

    weather = state["weather"]
    if weather is not None and not _valid_weather(weather):
        return False
    for key in ("last_full", "weather_time", "weather_bucket", "last_ntp"):
        value = state[key]
        if value is not None and not _is_integer(value):
            return False
    return True


def encode(state: "dict[str, Any]") -> bytes:
    body = json.dumps(state, separators=(",", ":")).encode("utf-8")
    raw = MAGIC + ("%08x:" % _checksum(body)).encode("ascii") + body
    if len(raw) > MAX_BYTES:
        raise ValueError("retained state is %d bytes; RTC limit is %d" % (len(raw), MAX_BYTES))
    return raw


def decode(
    raw: bytes, expected_fingerprint: str, expected_sections: int,
) -> "dict[str, Any] | None":
    try:
        if not raw or len(raw) > MAX_BYTES or not raw.startswith(MAGIC):
            return None
        split = len(MAGIC) + 8
        if len(raw) <= split or raw[split:split + 1] != b":":
            return None
        expected = int(raw[len(MAGIC):split], 16)
        body = raw[split + 1:]
        if _checksum(body) != expected:
            return None
        state = json.loads(body.decode("utf-8"))
        if not _valid_state(state, expected_fingerprint, expected_sections):
            return None
        return state
    except Exception:
        return None
