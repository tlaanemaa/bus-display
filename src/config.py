"""Load validated Wi-Fi credentials from the on-device configuration."""
import json

if False:
    from models import WifiConfig

PATH = "/config.json"


class ConfigError(ValueError):
    """The on-device Wi-Fi configuration is malformed."""


def load() -> "WifiConfig | None":
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except OSError:
        return None
    except ValueError as exc:
        raise ConfigError("config.json is not valid JSON: %s" % exc)

    if not isinstance(raw, dict):
        raise ConfigError("config.json must be an object")
    if "wifi" not in raw or raw["wifi"] is None:
        return None
    wifi = raw["wifi"]
    if not isinstance(wifi, dict):
        raise ConfigError("wifi must be an object")
    ssid = wifi.get("ssid")
    password = wifi.get("password")
    if not isinstance(ssid, str):
        raise ConfigError("wifi.ssid must be a string")
    if not isinstance(password, str):
        raise ConfigError("wifi.password must be a string")
    return {"ssid": ssid, "password": password}
