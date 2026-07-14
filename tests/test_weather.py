"""Host-side tests for the pure parse/format logic in weather.py.
Fixtures are trimmed real shapes from Open-Meteo's forecast endpoint
requested with daily + hourly fields + forecast_days=1 (see openmeteo.py).
condition/precip come from the hourly block now, restricted to the
07:00-23:00 daytime window -- see weather.py's module docstring for why
(Open-Meteo's own daily weather_code/precip fields are a full-24h MAX,
which was reporting "cloudy" on clear days over a single overnight hour)."""
import weather


def _hour(h, code, precip=None):
    return ("2026-07-12T%02d:00" % h, code, precip)


def _raw(hours, tmax=12.4, tmin=5.6, date="2026-07-12"):
    """hours: list of (time, code, precip) tuples, e.g. from _hour()."""
    daily = {"time": [date], "temperature_2m_max": [tmax], "temperature_2m_min": [tmin]}
    hourly = {
        "time": [h[0] for h in hours],
        "weather_code": [h[1] for h in hours],
        "precipitation_probability": [h[2] for h in hours],
    }
    return {"daily": daily, "hourly": hourly}


def _all_day(code, precip=None):
    """One code repeated at every hour 0-23 -- simplest "whole day is X" fixture."""
    return [_hour(h, code, precip) for h in range(24)]


def test_parse_basic_fields_and_rounding():
    w = weather.parse_weather(_raw(_all_day(3, 40), tmax=12.4, tmin=5.6))
    assert w == {"condition": "cloudy", "tmax": 12, "tmin": 6, "precip": 40,
                 "date": "2026-07-12"}


def test_daytime_mode_ignores_overnight_condition():
    # The exact bug this fixes: clear all day, but ONE overnight hour (3 AM,
    # outside 07:00-23:00) is overcast. Open-Meteo's own daily aggregate
    # (a full-24h max) would report "cloudy" for the whole day; our daytime
    # mode must ignore the 3 AM sample and report "clear".
    hours = _all_day(0)  # clear all 24 hours
    hours[3] = _hour(3, 3)  # 3 AM overcast
    w = weather.parse_weather(_raw(hours))
    assert w["condition"] == "clear"


def test_daytime_mode_is_most_common_bucket_in_window():
    hours = _all_day(0)  # clear baseline
    for h in range(7, 23):  # whole daytime window
        hours[h] = _hour(h, 61)  # rain
    # flip a few daytime hours back to clear so rain is still the majority
    hours[7] = _hour(7, 0)
    hours[8] = _hour(8, 0)
    w = weather.parse_weather(_raw(hours))
    assert w["condition"] == "rain"


def test_daytime_mode_tie_breaks_toward_more_severe():
    hours = _all_day(0)
    # 8 clear vs 8 rain within the daytime window -> tie, rain wins (more severe)
    for h in range(7, 15):
        hours[h] = _hour(h, 0)  # clear
    for h in range(15, 23):
        hours[h] = _hour(h, 61)  # rain
    w = weather.parse_weather(_raw(hours))
    assert w["condition"] == "rain"


def test_precip_is_daytime_max_not_full_day():
    hours = _all_day(0, precip=5)
    hours[3] = _hour(3, 0, precip=90)  # overnight spike, outside the window
    hours[14] = _hour(14, 0, precip=30)  # daytime, should win
    w = weather.parse_weather(_raw(hours))
    assert w["precip"] == 30


def test_daytime_mode_skips_null_weather_code():
    # Open-Meteo can null an hourly value. A null code must be skipped, not
    # counted as "cloudy" (condition_for_code(None) -> "cloudy") -- otherwise
    # one null daytime hour skews the mode. Clear all day except a null at
    # noon -> still clear.
    hours = _all_day(0)
    hours[12] = _hour(12, None)
    w = weather.parse_weather(_raw(hours))
    assert w["condition"] == "clear"


def test_parse_extracts_forecast_date_for_staleness_check():
    # The forecast's own date is carried through so the caller can tell a
    # kept last-good reading is still today's vs. from a prior day.
    w = weather.parse_weather(_raw(_all_day(0)))
    assert w["date"] == "2026-07-12"
    # Missing time array -> date None (caller treats that as not-today).
    raw = _raw(_all_day(0))
    del raw["daily"]["time"]
    assert weather.parse_weather(raw)["date"] is None


def test_rain_intensity_buckets():
    # WMO intensity maps to the three rain glyphs (the umbrella-decision axis).
    assert weather.condition_for_code(51) == "drizzle"   # light drizzle
    assert weather.condition_for_code(61) == "rain"      # slight rain
    assert weather.condition_for_code(63) == "rain"      # moderate rain
    assert weather.condition_for_code(65) == "rain_heavy"  # heavy rain
    assert weather.condition_for_code(80) == "rain"      # slight showers
    assert weather.condition_for_code(82) == "rain_heavy"  # violent showers


def test_condition_buckets_cover_the_glyph_set():
    assert weather.condition_for_code(0) == "clear"
    assert weather.condition_for_code(2) == "partly"
    assert weather.condition_for_code(45) == "fog"
    assert weather.condition_for_code(71) == "snow"
    assert weather.condition_for_code(95) == "thunder"


def test_unknown_code_is_cloudy_not_a_crash():
    assert weather.condition_for_code(123) == "cloudy"
    assert weather.condition_for_code(None) == "cloudy"
    assert weather.condition_for_code("x") == "cloudy"


def test_parse_missing_or_empty_returns_none():
    assert weather.parse_weather(None) is None
    assert weather.parse_weather({}) is None
    assert weather.parse_weather({"daily": {}}) is None
    # temps present but no hourly block -> unusable
    assert weather.parse_weather({"daily": {"temperature_2m_max": [10],
                                            "temperature_2m_min": [4]}}) is None
    # hourly block present but no daytime samples (all hours outside the
    # 07:00-23:00 window) -> unusable
    raw = _raw([_hour(3, 0)])
    assert weather.parse_weather(raw) is None


def test_parse_precip_optional():
    w = weather.parse_weather(_raw(_all_day(0)))  # no precip values anywhere
    assert w["precip"] is None


def test_is_for_today_keeps_todays_reading_only():
    today = "2026-07-12"
    reading = weather.parse_weather(_raw(_all_day(0)))   # date 2026-07-12
    assert weather.is_for_today(reading, today) is True
    # A prior-day reading is stale -> not today (drives the "Weather error"
    # fallback across midnight during an outage).
    stale = dict(reading, date="2026-07-11")
    assert weather.is_for_today(stale, today) is False
    # No reading at all, or a reading with no date, is not today.
    assert weather.is_for_today(None, today) is False
    assert weather.is_for_today(dict(reading, date=None), today) is False


def test_keep_last_good_requires_today_and_fresh():
    today = "2026-07-12"
    reading = weather.parse_weather(_raw(_all_day(0)))   # date 2026-07-12
    cap = 180 * 60  # 3h
    # Today's forecast, fresh -> keep it over the error.
    assert weather.keep_last_good(reading, today, 60 * 60, cap) is True
    assert weather.keep_last_good(reading, today, cap, cap) is True   # exactly at the cap
    # Today's forecast but too old -> fall back to the error (the daily
    # forecast revises through the day, so a stale reading isn't trustworthy).
    assert weather.keep_last_good(reading, today, cap + 1, cap) is False
    # Prior-day reading is never kept, even if "fresh" by age.
    stale = dict(reading, date="2026-07-11")
    assert weather.keep_last_good(stale, today, 60, cap) is False
    # No prior good fetch (age None) or no reading -> not usable.
    assert weather.keep_last_good(reading, today, None, cap) is False
    assert weather.keep_last_good(None, today, 60, cap) is False


def test_format_temps_low_first():
    w = {"condition": "rain", "tmin": 6, "tmax": 12, "precip": 60}
    assert weather.format_temps(w) == "6° / 12°"


def test_format_precip_threshold():
    # Suppressed below the threshold (the icon already says "dry"),
    # shown at/above it.
    assert weather.format_precip({"precip": 5}) is None
    assert weather.format_precip({"precip": None}) is None
    assert weather.format_precip({"precip": weather.PRECIP_SHOW_THRESHOLD}) == "10%"
    assert weather.format_precip({"precip": 60}) == "60%"


def test_summary_text_is_stable_for_change_detection():
    w = {"condition": "rain", "tmin": 6, "tmax": 12, "precip": 60}
    assert weather.summary_text(w) == "weather: rain 6° / 12°  60%"
    assert weather.summary_text(None) == "weather: n/a"
    # low precip omitted from the key too
    dry = {"condition": "clear", "tmin": 11, "tmax": 20, "precip": 5}
    assert weather.summary_text(dry) == "weather: clear 11° / 20°"
