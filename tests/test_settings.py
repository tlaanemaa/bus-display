import pytest
import config
import settings


def valid_settings():
    return {
        "stops": [{"name": "Slussen", "site_id": 9192}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "full_refresh_interval_min": 60,
        "power": {"wake_advance_s": 3},
        "weather": {
            "enabled": True,
            "latitude": 59.33,
            "longitude": 18.06,
            "pull_interval_min": 30,
            "max_age_min": 180,
        },
    }


def test_validate_applies_current_defaults():
    cfg = settings.validate({"stops": [{"name": "Slussen", "site_id": 9192}]})
    assert cfg["direction_code"] == 2
    assert cfg["forecast_min"] == 180
    assert cfg["departures_per_stop"] == 3
    assert cfg["full_refresh_interval_min"] == 60
    assert cfg["power"] == {"wake_advance_s": 3}
    assert cfg["weather"] is None


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(stops=[]),
    lambda c: c.update(stops=c["stops"] * 3),
    lambda c: c.update(direction_code=0),
    lambda c: c.update(forecast_min=1201),
    lambda c: c.update(departures_per_stop=0),
    lambda c: c.update(full_refresh_interval_min=0),
    lambda c: c["power"].update(wake_advance_s=60),
])
def test_validate_rejects_invalid_runtime_values(mutate):
    raw = valid_settings()
    mutate(raw)
    with pytest.raises(ValueError):
        settings.validate(raw)


def test_validate_rejects_removed_mode_and_cadence_keys():
    raw = valid_settings()
    raw["power"]["deep_sleep"] = True
    raw["render_interval_min"] = 1
    with pytest.raises(ValueError, match="unsupported setting"):
        settings.validate(raw)


def test_validate_requires_complete_enabled_weather():
    raw = valid_settings()
    del raw["weather"]["longitude"]
    with pytest.raises(ValueError, match="weather.longitude"):
        settings.validate(raw)


def test_config_load_returns_validated_wifi(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"wifi": {"ssid": "network", "password": "secret"}}')
    monkeypatch.setattr(config, "PATH", str(path))
    assert config.load() == {"ssid": "network", "password": "secret"}


def test_config_load_handles_missing_or_invalid_wifi(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "PATH", str(path))
    assert config.load() is None
    path.write_text('{"wifi": {"ssid": "network", "password": 3}}')
    with pytest.raises(config.ConfigError, match="wifi.password"):
        config.load()
