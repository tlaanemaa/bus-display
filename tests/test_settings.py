import json

import pytest

import config
import settings


def valid_settings():
    return {
        "stops": [{"name": "Slussen", "site_id": 9192}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 60,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
        "weather": {
            "enabled": True,
            "latitude": 59.33,
            "longitude": 18.06,
            "pull_interval_min": 30,
            "max_age_min": 180,
        },
    }


def test_validate_preserves_existing_settings_shape_and_defaults():
    raw = valid_settings()
    assert settings.validate(raw) == raw
    minimal = {"stops": [{"name": "Slussen", "site_id": 9192}]}
    validated = settings.validate(minimal)
    assert validated["direction_code"] == 2
    assert validated["forecast_min"] == 180
    assert validated["departures_per_stop"] == 3
    assert validated["data_pull_interval_min"] == 1
    assert validated["render_interval_min"] == 1
    assert validated["full_refresh_interval_min"] == 30
    assert validated["power"] == {"deep_sleep": False, "wake_advance_s": 3}


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(stops=[]),
    lambda x: x["stops"][0].update(name=""),
    lambda x: x["stops"][0].update(site_id=0),
    lambda x: x.update(direction_code=3),
    lambda x: x.update(forecast_min=1201),
    lambda x: x.update(departures_per_stop=4),
    lambda x: x.update(render_interval_min=0),
    lambda x: x["power"].update(deep_sleep="yes"),
    lambda x: x["power"].update(wake_advance_s=60),
    lambda x: x["weather"].update(latitude=91),
])
def test_validate_rejects_values_that_can_break_runtime(mutate):
    raw = valid_settings()
    mutate(raw)
    with pytest.raises(settings.SettingsError):
        settings.validate(raw)


def test_validate_rejects_nan_weather_coordinates():
    raw = valid_settings()
    raw["weather"]["latitude"] = float("nan")
    with pytest.raises(settings.SettingsError):
        settings.validate(raw)


def test_validate_keeps_unknown_keys_and_returns_copied_nested_values():
    raw = valid_settings()
    raw["legacy"] = {"modes": ["old"]}
    validated = settings.validate(raw)
    validated["stops"][0]["name"] = "Changed"
    validated["power"]["deep_sleep"] = False
    validated["legacy"]["modes"].append("new")
    assert raw["stops"][0]["name"] == "Slussen"
    assert raw["power"]["deep_sleep"] is True
    assert raw["legacy"] == {"modes": ["old"]}


def test_config_accepts_existing_nested_wifi_and_missing_file(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"wifi": {"ssid": "home", "password": "secret"}}))
    monkeypatch.setattr(config, "PATH", str(path))
    assert config.load() == {"wifi": {"ssid": "home", "password": "secret"}}
    monkeypatch.setattr(config, "PATH", str(tmp_path / "missing.json"))
    assert config.load() == {}


@pytest.mark.parametrize("raw", [[], {"wifi": []}, {"wifi": {"ssid": 7}}, {"wifi": {"ssid": "x", "password": 7}}])
def test_config_rejects_malformed_credentials(raw):
    with pytest.raises(config.ConfigError):
        config.validate(raw)


def test_config_defaults_password_and_copies_wifi_mapping():
    raw = {"wifi": {"ssid": "home"}, "legacy": {"value": 1}}
    validated = config.validate(raw)
    validated["wifi"]["ssid"] = "other"
    validated["legacy"]["value"] = 2
    assert validated["wifi"]["password"] == ""
    assert raw == {"wifi": {"ssid": "home"}, "legacy": {"value": 1}}


def test_settings_load_keeps_validation_error_message(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"stops": []}))
    monkeypatch.setattr(settings, "PATH", str(path))
    with pytest.raises(settings.SettingsError, match="one or two stops"):
        settings.load()


def test_settings_load_distinguishes_missing_and_invalid_json(monkeypatch, tmp_path):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(settings, "PATH", str(missing))
    with pytest.raises(settings.SettingsError, match="missing on device"):
        settings.load()
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    monkeypatch.setattr(settings, "PATH", str(malformed))
    with pytest.raises(settings.SettingsError, match="not valid JSON"):
        settings.load()


def test_config_load_keeps_validation_error_message(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"wifi": {"ssid": 7}}))
    monkeypatch.setattr(config, "PATH", str(path))
    with pytest.raises(config.ConfigError, match="wifi.ssid must be a string"):
        config.load()


def test_config_load_rejects_invalid_json(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{")
    monkeypatch.setattr(config, "PATH", str(path))
    with pytest.raises(config.ConfigError, match="not valid JSON"):
        config.load()
