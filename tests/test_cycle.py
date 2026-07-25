"""Host tests for the pure one-wake display policy."""
import cycle
import retained


TODAY = "2026-07-25"
FOOTER = ["Lor 25 jul 14:32"]


def _settings(site_id=9192, weather_cfg=None):
    return {
        "stops": [{"name": "Slussen", "site_id": site_id}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 60,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
        "weather": weather_cfg,
    }


def _weather_settings():
    return {
        "enabled": True,
        "latitude": 59.33,
        "longitude": 18.06,
        "pull_interval_min": 30,
        "max_age_min": 180,
    }


def _reading(date=TODAY):
    return {
        "date": date,
        "condition": "clear",
        "tmin": 12,
        "tmax": 21,
        "precip": 5,
    }


def _section(stop_key="9192:2", destination="No departures", stale=False):
    return {
        "stop_key": stop_key,
        "name": "Slussen",
        "hero_main": None,
        "hero_unit": None,
        "badge_line": None,
        "dest": destination,
        "rows": [],
        "stale": stale,
    }


def _state(
    cfg=None,
    section=None,
    footer=None,
    status=None,
    last_full=100,
    weather_reading=None,
    weather_time=None,
):
    cfg = cfg or _settings()
    return {
        "v": retained.RETAINED_VERSION,
        "render_rev": retained.RENDER_REVISION,
        "settings": retained.settings_fingerprint(cfg),
        "frame": {
            "sections": [section or _section()],
            "footer": footer or ["Lor 25 jul 14:31"],
            "status": status or {"kind": "none"},
        },
        "last_full": last_full,
        "weather": weather_reading,
        "weather_time": weather_time,
        "last_ntp": 50,
    }


def _decide(**overrides):
    args = {
        "settings": _settings(),
        "previous": None,
        "departure_results": [[]],
        "connected": True,
        "weather_attempted": False,
        "weather_result": None,
        "now_epoch": 200,
        "today_iso": TODAY,
        "footer": FOOTER,
        "last_ntp_epoch": 150,
    }
    args.update(overrides)
    return cycle.decide(**args)


def test_stop_key_is_stable_site_and_direction_identity():
    assert cycle.stop_key({"name": "Renamed", "site_id": 9192}, 2) == "9192:2"


def test_successful_empty_departures_are_fresh():
    decision = _decide(departure_results=[[]])

    section = decision["frame"]["sections"][0]
    assert section["dest"] == "No departures"
    assert section["stale"] is False


def test_failed_stop_reuses_only_matching_stop_identity():
    previous = _state(section=_section(destination="Old"))

    decision = _decide(previous=previous, departure_results=[None])

    section = decision["frame"]["sections"][0]
    assert section["dest"] == "Old"
    assert section["stale"] is True


def test_failed_stop_fallback_is_keyed_instead_of_positional():
    cfg = _settings()
    cfg["stops"] = [
        {"name": "Slussen", "site_id": 9192},
        {"name": "Nacka", "site_id": 1234},
    ]
    previous = _state(cfg=cfg)
    previous["frame"]["sections"] = [
        dict(_section("1234:2", "Wrong position"), name="Nacka"),
        _section("9192:2", "Matching identity"),
    ]

    decision = _decide(
        settings=cfg,
        previous=previous,
        departure_results=[None, []],
    )

    assert decision["frame"]["sections"][0]["dest"] == "Matching identity"
    assert decision["frame"]["sections"][0]["stale"] is True


def test_failed_reconfigured_stop_does_not_reuse_old_departures():
    old_cfg = _settings()
    previous = _state(
        cfg=old_cfg,
        section=_section(stop_key="9192:2", destination="Old"),
    )
    new_cfg = _settings(site_id=1234)

    decision = _decide(
        settings=new_cfg,
        previous=previous,
        departure_results=[None],
    )

    section = decision["frame"]["sections"][0]
    assert section["dest"] == "No departures"
    assert section["stale"] is True
    assert decision["refresh"] == "full"


def test_refresh_selection_is_none_partial_then_full():
    same = _state(footer=FOOTER)
    recent = _state()
    old_full = _state(last_full=0)

    assert _decide(previous=same)["refresh"] == "none"
    assert _decide(previous=recent, now_epoch=200)["refresh"] == "partial"
    assert _decide(previous=old_full, now_epoch=4000)["refresh"] == "full"


def test_full_refresh_is_never_selected_for_unchanged_content():
    previous = _state(footer=FOOTER, last_full=0)

    decision = _decide(previous=previous, now_epoch=4000)

    assert decision["refresh"] == "none"
    assert decision["state"]["last_full"] == 0


def test_full_refresh_updates_last_full_but_partial_does_not():
    full = _decide(previous=_state(last_full=0), now_epoch=4000)
    partial = _decide(previous=_state(last_full=100), now_epoch=200)

    assert full["state"]["last_full"] == 4000
    assert partial["state"]["last_full"] == 100


def test_weather_success_is_retained_at_supplied_epoch():
    cfg = _settings(weather_cfg=_weather_settings())
    reading = _reading()

    decision = _decide(
        settings=cfg,
        weather_attempted=True,
        weather_result=reading,
        now_epoch=500,
    )

    assert decision["frame"]["status"] == {"kind": "weather", "reading": reading}
    assert decision["state"]["weather"] == reading
    assert decision["state"]["weather_time"] == 500


def test_failed_weather_attempt_keeps_valid_same_day_reading():
    cfg = _settings(weather_cfg=_weather_settings())
    reading = _reading()
    previous = _state(
        cfg=cfg,
        weather_reading=reading,
        weather_time=100,
        status={"kind": "weather", "reading": reading},
    )

    decision = _decide(
        settings=cfg,
        previous=previous,
        weather_attempted=True,
        weather_result=None,
        now_epoch=200,
    )

    assert decision["frame"]["status"] == {"kind": "weather", "reading": reading}
    assert decision["state"]["weather"] == reading
    assert decision["state"]["weather_time"] == 100


def test_unattempted_weather_also_requires_a_fresh_fallback():
    cfg = _settings(weather_cfg=_weather_settings())
    previous = _state(
        cfg=cfg,
        weather_reading=_reading(),
        weather_time=100,
    )

    decision = _decide(
        settings=cfg,
        previous=previous,
        weather_attempted=False,
        now_epoch=11000,
    )

    assert decision["frame"]["status"] == {"kind": "weather_error"}
    assert decision["state"]["weather"] is None
    assert decision["state"]["weather_time"] is None


def test_previous_day_weather_is_rejected_even_when_recent():
    cfg = _settings(weather_cfg=_weather_settings())
    previous = _state(
        cfg=cfg,
        weather_reading=_reading("2026-07-24"),
        weather_time=190,
    )

    decision = _decide(settings=cfg, previous=previous, now_epoch=200)

    assert decision["frame"]["status"] == {"kind": "weather_error"}
    assert decision["state"]["weather"] is None
    assert decision["state"]["weather_time"] is None


def test_disabling_weather_clears_retained_reading():
    weather_cfg = _settings(weather_cfg=_weather_settings())
    previous = _state(
        cfg=weather_cfg,
        weather_reading=_reading(),
        weather_time=100,
        status={"kind": "weather", "reading": _reading()},
    )

    decision = _decide(settings=_settings(), previous=previous)

    assert decision["frame"]["status"] == {"kind": "none"}
    assert decision["state"]["weather"] is None
    assert decision["state"]["weather_time"] is None


def test_wifi_error_has_priority_over_weather_error():
    cfg = _settings(weather_cfg=_weather_settings())

    decision = _decide(
        settings=cfg,
        connected=False,
        departure_results=[None],
    )

    assert decision["frame"]["status"] == {"kind": "wifi_error"}


def test_future_weather_timestamp_is_due():
    assert cycle.weather_due(100, 200, 1800) is True


def test_weather_due_uses_elapsed_interval_boundary():
    assert cycle.weather_due(100, None, 1800) is True
    assert cycle.weather_due(1899, 100, 1800) is False
    assert cycle.weather_due(1900, 100, 1800) is True


def test_supplied_ntp_epoch_is_carried_without_policy_clock_access():
    previous = _state()

    decision = _decide(previous=previous, last_ntp_epoch=777)

    assert decision["state"]["last_ntp"] == 777
