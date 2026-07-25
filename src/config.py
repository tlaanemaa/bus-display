"""Load and save the temporary legacy Wi-Fi configuration root."""
import json

if False:
    from models import ConfigRoot

PATH = "/config.json"


class ConfigError(ValueError):
    """The on-device Wi-Fi configuration is malformed."""


def _validate(raw: object) -> "ConfigRoot":
    if not isinstance(raw, dict):
        raise ConfigError("config.json must be an object")
    for key in raw:
        if not isinstance(key, str) or key != "wifi":
            raise ConfigError("unsupported setting: config.%s" % key)
    if "wifi" not in raw:
        return {}
    wifi = raw["wifi"]
    if not isinstance(wifi, dict):
        raise ConfigError("wifi must be an object")
    for key in wifi:
        if not isinstance(key, str) or key not in {"ssid", "password"}:
            raise ConfigError("unsupported setting: wifi.%s" % key)
    ssid = wifi.get("ssid")
    password = wifi.get("password")
    if not isinstance(ssid, str):
        raise ConfigError("wifi.ssid must be a string")
    if not isinstance(password, str):
        raise ConfigError("wifi.password must be a string")
    return {"wifi": {"ssid": ssid, "password": password}}


def load() -> "ConfigRoot":
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except OSError:
        return {}
    except ValueError as exc:
        raise ConfigError("config.json is not valid JSON: %s" % exc)
    return _validate(raw)


def save(config: "ConfigRoot") -> None:
    with open(PATH, "w") as f:
        json.dump(_validate(config), f)
