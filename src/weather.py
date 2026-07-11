"""Pure logic: turn an Open-Meteo forecast JSON into the small "today"
summary the footer draws -- condition bucket, high/low, precipitation
chance. No hardware or network imports, so it runs under host pytest (see
CLAUDE.md "Testability rule"); openmeteo.py does the fetching.

Why Open-Meteo and not SMHI (the obvious Swedish source): SMHI's point
forecast returns the whole multi-day hourly series (100KB+), and parsing
that on this PSRAM-less board reruns the RAM-vs-HTTPS fight the SL fetch
already had to win (CLAUDE.md "RAM-vs-HTTPS conflict"). Open-Meteo lets us
request ONLY today's daily fields -> ~1KB. Keyless, same as SL.

The condition strings returned here are exactly the keys display.py's
_WEATHER_DRAWERS dispatches on -- keep the two in sync. Rain is split
three ways (drizzle / rain / rain_heavy) because intensity is what decides
the umbrella, and WMO weather codes already carry it (see _CODE)."""


# WMO weather interpretation codes -> our glyph buckets. Ranges collapsed
# to the handful of icons a glance display can distinguish. Intensity is
# preserved for rain (the decision-relevant axis) and flattened elsewhere
# (one snow icon, one fog icon -- extra snow resolution wouldn't change
# what you do). Unlisted codes fall back to "cloudy" (see condition_for_code).
_CODE = {
    0: "clear", 1: "clear",
    2: "partly",
    3: "cloudy",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle",
    56: "drizzle", 57: "drizzle",            # freezing drizzle
    61: "rain", 63: "rain",
    65: "rain_heavy",
    66: "rain", 67: "rain_heavy",            # freezing rain
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "rain", 81: "rain",
    82: "rain_heavy",                        # violent showers
    85: "snow", 86: "snow",
    95: "thunder", 96: "thunder", 99: "thunder",
}


def condition_for_code(code):
    """WMO code -> a glyph bucket string (display._WEATHER_DRAWERS key).
    Unknown/missing codes -> "cloudy" (a safe, non-alarming default)."""
    try:
        return _CODE.get(int(code), "cloudy")
    except (TypeError, ValueError):
        return "cloudy"


def _first(daily, key):
    """First (today's) value of an Open-Meteo daily array, or None."""
    v = daily.get(key)
    if isinstance(v, list) and v:
        return v[0]
    return None


def parse_weather(raw_json):
    """raw_json: Open-Meteo response with a `daily` block requested for a
    single day (forecast_days=1). Returns a small dict --
    {condition, tmax, tmin, precip} -- or None if the payload is unusable
    (so the caller keeps last-good / draws no weather). Temps are rounded
    to whole degrees (a glance display; the decimal is noise); precip is
    the max precipitation probability for the day in %, or None if absent."""
    if not raw_json:
        return None
    daily = raw_json.get("daily")
    if not isinstance(daily, dict):
        return None
    code = _first(daily, "weather_code")
    tmax = _first(daily, "temperature_2m_max")
    tmin = _first(daily, "temperature_2m_min")
    if code is None or tmax is None or tmin is None:
        return None
    precip = _first(daily, "precipitation_probability_max")
    return {
        "condition": condition_for_code(code),
        "tmax": int(round(tmax)),
        "tmin": int(round(tmin)),
        "precip": None if precip is None else int(round(precip)),
    }


def format_temps(weather):
    """High/low as one string, e.g. "6° / 12°" -- low first (the
    number that decides the jacket), matching the mockup."""
    return "%d° / %d°" % (weather["tmin"], weather["tmax"])


# Below this precipitation probability the cue is suppressed entirely (the
# condition icon already says "dry"); showing "5%" is noise on a glance
# display. Tuned to the design intent: surface rain only when it's a real
# possibility worth an umbrella.
PRECIP_SHOW_THRESHOLD = 20


def format_precip(weather):
    """Precipitation cue string (e.g. "60%"), or None when it's low enough
    to omit -- see PRECIP_SHOW_THRESHOLD."""
    p = weather.get("precip")
    if p is None or p < PRECIP_SHOW_THRESHOLD:
        return None
    return "%d%%" % p


def summary_text(weather):
    """One compact line for serial logging + render change-detection
    (CLAUDE.md "make the code corroborate the screen"). Not drawn."""
    if not weather:
        return "weather: n/a"
    p = format_precip(weather)
    return "weather: %s %s%s" % (
        weather["condition"], format_temps(weather), ("  " + p) if p else "")
