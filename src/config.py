"""Load/save the on-device /config.json.

An absent file just means "not configured yet" -- Wi-Fi credentials never
exist anywhere but this file on the device (see AGENTS.md). This is
deliberately Wi-Fi-only: stop ids/direction/refresh settings live in
/settings.json instead (see settings.py) -- both are runtime JSON, not
code, but kept as two separate files since only settings.json needs to be
gitignored (see AGENTS.md "Departures logic & stops").
"""
import json

if False:
    from typing import Any

PATH = "/config.json"


class ConfigError(ValueError):
    """The configuration file cannot be used safely by the runtime."""


def _copy(value: object) -> object:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def validate(raw: object) -> "dict[str, Any]":
    """Copy and validate the optional legacy Wi-Fi configuration."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be an object")
    cfg = _copy(raw)
    if not isinstance(cfg, dict):
        raise ConfigError("config must be an object")
    if "wifi" not in cfg:
        return cfg

    wifi = cfg["wifi"]
    if not isinstance(wifi, dict):
        raise ConfigError("wifi must be an object")
    ssid = wifi.get("ssid")
    if not isinstance(ssid, str):
        raise ConfigError("wifi.ssid must be a string")
    password = wifi.get("password", "")
    if not isinstance(password, str):
        raise ConfigError("wifi.password must be a string")
    wifi["password"] = password
    return cfg


def load() -> "dict[str, Any]":
    try:
        with open(PATH) as f:
            return validate(json.load(f))
    except OSError:
        return {}
    except ValueError as e:
        raise ConfigError("config.json is not valid JSON") from e


def save(config: "dict[str, Any]") -> None:
    with open(PATH, "w") as f:
        json.dump(config, f)
