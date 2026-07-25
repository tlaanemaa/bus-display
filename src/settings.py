"""Load and validate the on-device departure-display settings."""
import json

if False:
    from models import Settings, StopConfig, WeatherConfig

PATH = "/settings.json"

_TOP_LEVEL_KEYS = {
    "stops", "direction_code", "forecast_min", "departures_per_stop",
    "full_refresh_interval_min",
    "power", "weather",
}
_POWER_KEYS = {"wake_advance_s"}
_WEATHER_KEYS = {
    "enabled", "latitude", "longitude", "pull_interval_min", "max_age_min",
}


class SettingsError(ValueError):
    """The persisted settings cannot safely drive the display."""


def _mapping(value: object, name: str) -> "dict[str, object]":
    if not isinstance(value, dict):
        raise SettingsError("%s must be an object" % name)
    return value


def _check_keys(raw: "dict[str, object]", allowed: "set[str]", name: str) -> None:
    for key in raw:
        if not isinstance(key, str) or key not in allowed:
            raise SettingsError("unsupported setting: %s.%s" % (name, key))


def _required_int(raw: "dict[str, object]", key: str, name: str) -> int:
    if key not in raw:
        raise SettingsError("%s is required" % name)
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError("%s must be an integer" % name)
    return value


def _optional_int(raw: "dict[str, object]", key: str, default: int, name: str) -> int:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError("%s must be an integer" % name)
    return value


def _number(raw: "dict[str, object]", key: str, name: str) -> float:
    if key not in raw:
        raise SettingsError("%s is required" % name)
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError("%s must be a number" % name)
    number = float(value)
    if number != number:
        raise SettingsError("%s must not be NaN" % name)
    return number


def _optional_bool(raw: "dict[str, object]", key: str, default: bool, name: str) -> bool:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, bool):
        raise SettingsError("%s must be true or false" % name)
    return value


def _within(value: "int | float", low: "int | float", high: "int | float", name: str) -> None:
    if value < low or value > high:
        raise SettingsError("%s must be between %d and %d" % (name, low, high))


def _stops(raw: "dict[str, object]", direction_code: int) -> "list[StopConfig]":
    value = raw.get("stops")
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        raise SettingsError("stops must contain one or two entries")

    result = []  # type: list[StopConfig]
    identities = set()
    for index, item in enumerate(value):
        stop = _mapping(item, "stops[%d]" % index)
        _check_keys(stop, {"name", "site_id"}, "stops[%d]" % index)
        name = stop.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SettingsError("stops[%d].name must be a non-blank string" % index)
        site_id = _required_int(stop, "site_id", "stops[%d].site_id" % index)
        if site_id <= 0:
            raise SettingsError("stops[%d].site_id must be positive" % index)
        identity = (site_id, direction_code)
        if identity in identities:
            raise SettingsError("duplicate stop identity")
        identities.add(identity)
        result.append({"name": name, "site_id": site_id})
    return result


def _weather(raw: "dict[str, object]") -> "WeatherConfig | None":
    if "weather" not in raw:
        return None
    weather = _mapping(raw["weather"], "weather")
    _check_keys(weather, _WEATHER_KEYS, "weather")
    enabled = _optional_bool(weather, "enabled", True, "weather.enabled")

    if "pull_interval_min" in weather:
        pull_interval_min = _optional_int(weather, "pull_interval_min", 30, "weather.pull_interval_min")
        if pull_interval_min <= 0:
            raise SettingsError("weather.pull_interval_min must be positive")
    else:
        pull_interval_min = 30
    if "max_age_min" in weather:
        max_age_min = _optional_int(weather, "max_age_min", 180, "weather.max_age_min")
        if max_age_min <= 0:
            raise SettingsError("weather.max_age_min must be positive")
    else:
        max_age_min = 180

    if not enabled:
        if "latitude" in weather:
            latitude = _number(weather, "latitude", "weather.latitude")
            _within(latitude, -90, 90, "weather.latitude")
        if "longitude" in weather:
            longitude = _number(weather, "longitude", "weather.longitude")
            _within(longitude, -180, 180, "weather.longitude")
        return None

    latitude = _number(weather, "latitude", "weather.latitude")
    longitude = _number(weather, "longitude", "weather.longitude")
    _within(latitude, -90, 90, "weather.latitude")
    _within(longitude, -180, 180, "weather.longitude")
    return {
        "enabled": True,
        "latitude": latitude,
        "longitude": longitude,
        "pull_interval_min": pull_interval_min,
        "max_age_min": max_age_min,
    }


def validate(raw: object) -> "Settings":
    """Return a copied, normalized settings dictionary or raise SettingsError."""
    root = _mapping(raw, "settings")
    _check_keys(root, _TOP_LEVEL_KEYS, "settings")

    direction_code = _optional_int(root, "direction_code", 2, "direction_code")
    _within(direction_code, 1, 2, "direction_code")
    forecast_min = _optional_int(root, "forecast_min", 180, "forecast_min")
    _within(forecast_min, 1, 1200, "forecast_min")
    departures_per_stop = _optional_int(root, "departures_per_stop", 3, "departures_per_stop")
    _within(departures_per_stop, 1, 3, "departures_per_stop")
    full_refresh_interval_min = _optional_int(
        root, "full_refresh_interval_min", 60, "full_refresh_interval_min")
    if full_refresh_interval_min <= 0:
        raise SettingsError("full_refresh_interval_min must be positive")

    power = _mapping(root.get("power", {}), "power")
    _check_keys(power, _POWER_KEYS, "power")
    wake_advance_s = _optional_int(power, "wake_advance_s", 3, "power.wake_advance_s")
    _within(wake_advance_s, 0, 59, "power.wake_advance_s")

    return {
        "stops": _stops(root, direction_code),
        "direction_code": direction_code,
        "forecast_min": forecast_min,
        "departures_per_stop": departures_per_stop,
        "full_refresh_interval_min": full_refresh_interval_min,
        "power": {"wake_advance_s": wake_advance_s},
        "weather": _weather(root),
    }


def load() -> "Settings":
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except OSError:
        raise SettingsError(
            "settings.json missing; copy settings.example.json to settings.json"
        )
    except ValueError as exc:
        raise SettingsError("settings.json is not valid JSON: %s" % exc)
    return validate(raw)
