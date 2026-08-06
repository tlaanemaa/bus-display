"""Load /settings.json -- stop ids, direction filter, refresh cadence.

Deliberately NOT committed to git (see .gitignore: src/settings.json) so
the owner's home stop doesn't end up in a public repo. See
settings.example.json for the template/field reference, and AGENTS.md
"Departures logic & stops" for what each field means and how to find a
site id.
"""
import json

if False:
    from typing import Any

PATH = "/settings.json"

# Keeps the owner-controlled portion of a maximum two-stop semantic frame
# comfortably inside retained.MAX_BYTES (2048), even when every character
# expands to a non-BMP JSON escape on CPython. See the retained budget test.
MAX_STOP_NAME_CHARS = 32


class SettingsError(ValueError):
    """The settings file cannot be used safely by the runtime."""


def _copy(value: object) -> object:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def _integer(
    value: object, name: str, minimum: int, maximum: "int | None" = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(name + " must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise SettingsError(name + " is out of range")
    return value


def _number(
    value: object, name: str, minimum: float, maximum: float,
) -> "float | int":
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(name + " must be a number")
    if not minimum <= value <= maximum:
        raise SettingsError(name + " is out of range")
    return value


def validate(raw: object) -> "dict[str, Any]":
    """Copy and validate the legacy settings shape used by both run modes."""
    if not isinstance(raw, dict):
        raise SettingsError("settings must be an object")
    cfg = _copy(raw)
    if not isinstance(cfg, dict):
        raise SettingsError("settings must be an object")

    stops = cfg.get("stops")
    if not isinstance(stops, list) or not 1 <= len(stops) <= 2:
        raise SettingsError("stops must contain one or two stops")
    for stop in stops:
        if not isinstance(stop, dict):
            raise SettingsError("each stop must be an object")
        name = stop.get("name")
        if (not isinstance(name, str) or not name.strip()
                or len(name) > MAX_STOP_NAME_CHARS):
            raise SettingsError(
                "stop name must be a non-blank string of at most %d characters"
                % MAX_STOP_NAME_CHARS
            )
        _integer(stop.get("site_id"), "stop site_id", 1)

    cfg["direction_code"] = _integer(cfg.get("direction_code", 2), "direction_code", 1, 2)
    cfg["forecast_min"] = _integer(cfg.get("forecast_min", 180), "forecast_min", 1, 1200)
    cfg["departures_per_stop"] = _integer(
        cfg.get("departures_per_stop", 3), "departures_per_stop", 1, 3
    )
    cfg["data_pull_interval_min"] = _integer(
        cfg.get("data_pull_interval_min", 1), "data_pull_interval_min", 1
    )
    cfg["render_interval_min"] = _integer(
        cfg.get("render_interval_min", 1), "render_interval_min", 1
    )
    cfg["full_refresh_interval_min"] = _integer(
        cfg.get("full_refresh_interval_min", 30), "full_refresh_interval_min", 1
    )

    power = cfg.get("power", {})
    if not isinstance(power, dict):
        raise SettingsError("power must be an object")
    deep_sleep = power.get("deep_sleep", False)
    if not isinstance(deep_sleep, bool):
        raise SettingsError("power.deep_sleep must be a boolean")
    power["deep_sleep"] = deep_sleep
    power["wake_advance_s"] = _integer(power.get("wake_advance_s", 3), "power.wake_advance_s", 0, 59)
    cfg["power"] = power

    if "weather" in cfg:
        weather = cfg["weather"]
        if not isinstance(weather, dict):
            raise SettingsError("weather must be an object")
        if "enabled" in weather and not isinstance(weather["enabled"], bool):
            raise SettingsError("weather.enabled must be a boolean")
        if "latitude" in weather:
            _number(weather["latitude"], "weather.latitude", -90, 90)
        if "longitude" in weather:
            _number(weather["longitude"], "weather.longitude", -180, 180)
        if "pull_interval_min" in weather:
            _integer(weather["pull_interval_min"], "weather.pull_interval_min", 1)
        if "max_age_min" in weather:
            _integer(weather["max_age_min"], "weather.max_age_min", 1)

    return cfg


def load() -> "dict[str, Any]":
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except OSError as e:
        raise SettingsError(
            "settings.json missing on device -- copy src/settings.example.json "
            "to src/settings.json, fill in your stop(s), then: "
            "mpremote connect COM3 fs cp src/settings.json :settings.json"
        ) from e
    except ValueError as e:
        raise SettingsError("settings.json is not valid JSON") from e
    return validate(raw)
