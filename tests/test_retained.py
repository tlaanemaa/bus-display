import copy

import pytest

import retained
import settings


def _settings():
    return {
        "stops": [{"name": "Rosenmalm", "site_id": 1234}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 60,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
        "weather": None,
    }


def _weather():
    return {
        "date": "2026-07-19", "condition": "rain",
        "tmin": 12, "tmax": 20, "precip": 40,
    }


def _state(cfg=None):
    cfg = cfg or _settings()
    return {
        "v": retained.STATE_VERSION,
        "render_rev": retained.RENDER_REVISION,
        "settings": retained.settings_fingerprint(cfg),
        "frame": [[{
            "name": "Rosenmalm", "hero_main": "4", "hero_unit": "min",
            "badge_line": "474", "dest": "Slussen",
            "rows": [["440", "Slussen", "12 min"]], "stale": False,
        }], ["Son 19 jul 14:32"], None],
        "last_full": 123,
        "weather": None,
        "weather_time": None,
        "weather_bucket": None,
        "last_ntp": None,
    }


def _decode(raw, cfg=None):
    cfg = cfg or _settings()
    return retained.decode(
        raw, retained.settings_fingerprint(cfg), len(cfg["stops"]),
    )


def test_round_trip_is_strict_and_within_rtc_limit():
    state = _state()
    raw = retained.encode(state)
    assert len(raw) < retained.MAX_BYTES
    assert _decode(raw) == state


def test_old_version_renderer_and_settings_are_rejected():
    for key, value in (("v", 1), ("render_rev", 999), ("settings", "deadbeef")):
        state = _state()
        state[key] = value
        assert _decode(retained.encode(state)) is None


@pytest.mark.parametrize("key,value", [
    ("v", 2.0), ("v", True), ("render_rev", 1.0), ("render_rev", True),
])
def test_schema_markers_must_be_integers_not_equal_numeric_types(key, value):
    state = _state()
    state[key] = value
    assert _decode(retained.encode(state)) is None


def test_stop_reorder_or_identity_change_invalidates_state():
    cfg = _settings()
    cfg["stops"].append({"name": "Slussen", "site_id": 9192})
    state = _state(cfg)

    reordered = copy.deepcopy(cfg)
    reordered["stops"].reverse()
    assert retained.decode(
        retained.encode(state), retained.settings_fingerprint(reordered), 2,
    ) is None

    changed = copy.deepcopy(cfg)
    changed["stops"][0] = {"name": "Other", "site_id": 9999}
    assert retained.decode(
        retained.encode(state), retained.settings_fingerprint(changed), 2,
    ) is None


def test_fingerprint_tracks_only_render_compatibility_identity():
    cfg = _settings()
    same_render = copy.deepcopy(cfg)
    same_render["forecast_min"] = 1200
    same_render["render_interval_min"] = 5
    same_render["full_refresh_interval_min"] = 30
    same_render["power"]["wake_advance_s"] = 10
    assert retained.settings_fingerprint(same_render) == retained.settings_fingerprint(cfg)

    for key, value in (("direction_code", 1), ("departures_per_stop", 2)):
        changed = copy.deepcopy(cfg)
        changed[key] = value
        assert retained.settings_fingerprint(changed) != retained.settings_fingerprint(cfg)

    weather = copy.deepcopy(cfg)
    weather["weather"] = {"enabled": True, "latitude": 59.33, "longitude": 18.06}
    assert retained.settings_fingerprint(weather) != retained.settings_fingerprint(cfg)
    weather["weather"]["enabled"] = False
    assert retained.settings_fingerprint(weather) == retained.settings_fingerprint(cfg)


def test_corruption_and_malformed_envelope_are_rejected():
    raw = bytearray(retained.encode(_state()))
    raw[-1] ^= 1
    assert _decode(bytes(raw)) is None
    assert _decode(b"") is None
    assert _decode(b"wrong") is None


def test_malformed_nested_frame_is_rejected():
    state = _state()
    state["frame"][0][0]["rows"] = [["only", "two"]]
    assert _decode(retained.encode(state)) is None


@pytest.mark.parametrize("mutate", [
    lambda state: state.update(extra=True),
    lambda state: state["frame"].append(None),
    lambda state: state["frame"][0][0].update(extra="field"),
    lambda state: state["frame"][0][0].update(name=7),
    lambda state: state["frame"][0][0].update(stale=1),
    lambda state: state["frame"][0][0].update(hero_main=None),
    lambda state: state["frame"][1].clear(),
    lambda state: state["frame"][1].extend(["one", "two", "three"]),
    lambda state: state["frame"].__setitem__(2, "unknown"),
    lambda state: state.update(last_full=True),
    lambda state: state.update(weather_time=1.5),
])
def test_shapes_that_could_break_or_misrepresent_draw_are_rejected(mutate):
    state = _state()
    mutate(state)
    assert _decode(retained.encode(state)) is None


def test_no_departures_section_accepts_only_the_legitimate_nullable_hero_form():
    state = _state()
    section = state["frame"][0][0]
    section.update({
        "hero_main": None, "hero_unit": None, "badge_line": None,
        "dest": "No departures", "rows": [],
    })
    assert _decode(retained.encode(state)) == state

    for key, value in (("hero_unit", "min"), ("badge_line", "474"),
                       ("dest", "Something else"), ("rows", [["1", "X", "2 min"]])):
        malformed = copy.deepcopy(state)
        malformed["frame"][0][0][key] = value
        assert _decode(retained.encode(malformed)) is None


@pytest.mark.parametrize("reading", [
    {"date": "2026-07-19", "condition": "unknown", "tmin": 12, "tmax": 20, "precip": 40},
    {"date": "2026-07-19", "condition": "rain", "tmin": "12", "tmax": 20, "precip": 40},
    {"date": "2026-07-19", "condition": "rain", "tmin": 12, "tmax": 20, "precip": "40"},
    {"date": "2026-07-19", "condition": "rain", "tmin": 12, "tmax": 20},
])
def test_malformed_frame_or_separately_retained_weather_is_rejected(reading):
    for key in ("weather",):
        state = _state()
        state[key] = reading
        assert _decode(retained.encode(state)) is None
    state = _state()
    state["frame"][2] = reading
    assert _decode(retained.encode(state)) is None


def test_valid_weather_and_status_sentinels_are_accepted():
    for status in (None, "wifi_error", "error", _weather()):
        state = _state()
        state["frame"][2] = status
        state["weather"] = _weather()
        assert _decode(retained.encode(state)) == state

    # parse_weather deliberately preserves a missing date as None; it is
    # drawable now but cannot be reused as a last-good reading tomorrow.
    state = _state()
    state["weather"] = dict(_weather(), date=None)
    state["frame"][2] = state["weather"]
    assert _decode(retained.encode(state)) == state


def test_section_count_must_match_current_settings():
    state = _state()
    assert retained.decode(
        retained.encode(state), retained.settings_fingerprint(_settings()), 2,
    ) is None


def test_oversized_raw_is_rejected_before_json_decode(monkeypatch):
    def fail_if_called(_body):
        raise AssertionError("oversized RTC bytes reached JSON decoding")

    monkeypatch.setattr(retained.json, "loads", fail_if_called)
    assert _decode(b"x" * (retained.MAX_BYTES + 1)) is None


def test_oversize_state_is_rejected():
    state = _state()
    state["padding"] = "x" * retained.MAX_BYTES
    with pytest.raises(ValueError):
        retained.encode(state)


def test_json_canonicalizes_tuples_without_invalidating_state():
    state = _state()
    state["frame"][0][0]["rows"] = [("440", "Slussen", "12 min")]
    decoded = _decode(retained.encode(state))
    assert decoded is not None
    assert decoded["frame"][0][0]["rows"] == [["440", "Slussen", "12 min"]]


def test_realistic_two_stop_maximum_frame_fits_rtc_memory():
    cfg = _settings()
    cfg["stops"].append({"name": "Grisslinge", "site_id": 4321})
    state = _state(cfg)
    section = state["frame"][0][0]
    section["rows"] = [
        ["474", "Stockholm Slussen", "12 min"],
        ["440", "Orminge centrum", "18 min"],
    ]
    second = copy.deepcopy(section)
    second["name"] = "Grisslinge"
    state["frame"][0].append(second)
    state["frame"][1] = ["Sondag 19 juli", "14:32"]
    state["frame"][2] = _weather()
    state["weather"] = _weather()
    state["weather_time"] = 1784464320
    state["weather_bucket"] = 99136
    state["last_ntp"] = 1784460000
    assert len(retained.encode(state)) < retained.MAX_BYTES


def test_worst_case_accepted_stop_names_and_departure_count_fit_rtc_memory():
    max_name_chars = settings.MAX_STOP_NAME_CHARS
    assert max_name_chars == 48
    cfg = settings.validate({
        "stops": [
            {"name": "\U0001f600" * max_name_chars, "site_id": 1234},
            {"name": "\U0001f680" * max_name_chars, "site_id": 4321},
        ],
        "direction_code": 2,
        "forecast_min": 1200,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 1,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
        "weather": {
            "enabled": True, "latitude": 90, "longitude": 180,
            "pull_interval_min": 1, "max_age_min": 1,
        },
    })
    state = _state(cfg)
    first = state["frame"][0][0]
    first["name"] = cfg["stops"][0]["name"]
    first["hero_main"] = "1200"
    first["hero_unit"] = "min"
    first["badge_line"] = "999"
    first["dest"] = "Stockholm Slussen via centrum"
    first["rows"] = [
        ["474", "Stockholm Slussen via centrum", "119 min"],
        ["440", "Orminge centrum via Nacka", "120 min"],
    ]
    second = copy.deepcopy(first)
    second["name"] = cfg["stops"][1]["name"]
    state["frame"][0].append(second)
    state["frame"][1] = ["Sondag 19 juli", "14:32"]
    maximum_weather = {
        "date": "9999-12-31", "condition": "rain_heavy",
        "tmin": -999, "tmax": 999, "precip": 100,
    }
    state["frame"][2] = maximum_weather
    state["last_full"] = 2_147_483_647
    state["weather"] = maximum_weather
    state["weather_time"] = 2_147_483_647
    state["weather_bucket"] = 2_147_483_647
    state["last_ntp"] = 2_147_483_647
    encoded = retained.encode(state)
    assert len(encoded) < retained.MAX_BYTES
    assert retained.MAX_BYTES - len(encoded) == 73
    assert retained.decode(
        encoded, retained.settings_fingerprint(cfg), len(cfg["stops"]),
    ) is not None
