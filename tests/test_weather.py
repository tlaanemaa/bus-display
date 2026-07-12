"""Host-side tests for the pure parse/format logic in weather.py.
Fixtures are trimmed real shapes from Open-Meteo's forecast endpoint
requested with daily fields + forecast_days=1 (see openmeteo.py)."""
import weather


def _raw(code, tmax, tmin, precip=None):
    daily = {
        "time": ["2026-07-12"],
        "weather_code": [code],
        "temperature_2m_max": [tmax],
        "temperature_2m_min": [tmin],
    }
    if precip is not None:
        daily["precipitation_probability_max"] = [precip]
    return {"daily": daily}


def test_parse_basic_fields_and_rounding():
    w = weather.parse_weather(_raw(3, 12.4, 5.6, 40))
    assert w == {"condition": "cloudy", "tmax": 12, "tmin": 6, "precip": 40}


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
    # temps present but no code -> unusable
    assert weather.parse_weather({"daily": {"temperature_2m_max": [10],
                                            "temperature_2m_min": [4]}}) is None


def test_parse_precip_optional():
    w = weather.parse_weather(_raw(0, 20, 11))  # no precip field
    assert w["precip"] is None


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
