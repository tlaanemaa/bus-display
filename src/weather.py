"""Pure logic: turn an Open-Meteo forecast JSON into the small "today"
summary the footer draws -- condition bucket, high/low, precipitation
chance. No hardware or network imports, so it runs under host pytest (see
CLAUDE.md "Testability rule"); openmeteo.py does the fetching.

Why Open-Meteo and not SMHI (the obvious Swedish source): SMHI's point
forecast returns the whole multi-day hourly series (100KB+), and parsing
that on this PSRAM-less board reruns the RAM-vs-HTTPS fight the SL fetch
already had to win (CLAUDE.md "RAM-vs-HTTPS conflict"). Open-Meteo lets us
request ONLY today's daily + hourly fields -> a couple KB. Keyless, same
as SL.

The condition strings returned here are exactly the keys display.py's
_WEATHER_DRAWERS dispatches on -- keep the two in sync. Rain is split
three ways (drizzle / rain / rain_heavy) because intensity is what decides
the umbrella, and WMO weather codes already carry it (see _CODE).

Condition + precip are derived from HOURLY data restricted to a waking
window (07:00-23:00 local), not Open-Meteo's own `daily` weather_code/
precipitation_probability_max fields. Verified 2026-07-13: Open-Meteo's
daily aggregation takes the MAX (most severe) hourly code/value across the
FULL 24h day, so a single overcast or drizzly hour at 3 AM reported
"cloudy" for an otherwise clear day -- the whole day's glyph was held
hostage by hours nobody's awake for. Computing our own mode/max over only
07:00-23:00 fixes that; temps stay on the daily block (the overnight low
is still relevant for dressing)."""


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


# Waking-hours window for the daytime mode (see module docstring): a bucket
# only counts if its hour is in [DAYTIME_START_HOUR, DAYTIME_END_HOUR).
# 07:00 inclusive, 23:00 exclusive -- matches "outside of it we sleep".
DAYTIME_START_HOUR = 7
DAYTIME_END_HOUR = 23

# Tie-break priority when two+ conditions are equally common across the
# daytime window (possible with up to 16 hourly samples, e.g. 8 clear /
# 8 rain): higher rank wins. Biases toward the more notable condition,
# consistent with the app surfacing rather than hiding uncertain info
# (e.g. the per-stop STALE badge, the explicit "Weather error" text).
_SEVERITY = {
    "clear": 0, "partly": 1, "cloudy": 2, "fog": 3, "drizzle": 4,
    "rain": 5, "snow": 6, "rain_heavy": 7, "thunder": 8,
}


def dominant_condition(codes):
    """Most common glyph bucket among a list of hourly WMO codes (already
    restricted to the daytime window by the caller). Votes on the BUCKET
    (condition_for_code(c)), not the raw code, so e.g. slight vs moderate
    rain don't split the count. Ties broken by _SEVERITY (see above).
    Empty input -> "cloudy" (safe default, same as an unknown code)."""
    counts = {}
    for c in codes:
        bucket = condition_for_code(c)
        counts[bucket] = counts.get(bucket, 0) + 1
    best = None
    best_count = -1
    for bucket, n in counts.items():
        if n > best_count or (n == best_count and _SEVERITY.get(bucket, 0) > _SEVERITY.get(best, 0)):
            best = bucket
            best_count = n
    return best if best is not None else "cloudy"


def _daytime_hourly(hourly):
    """Pick (weather_code, precipitation_probability) pairs from an
    Open-Meteo `hourly` block whose local timestamp falls in the daytime
    window. `hourly["time"]` entries look like "2026-07-13T14:00" (local,
    since the fetch uses timezone=auto) -- hour is a fixed slice, no date
    parsing needed. Skips a sample if its weather_code or precip entry is
    missing/null (Open-Meteo can null an hourly value) -- a null code would
    otherwise vote as "cloudy" via condition_for_code and skew the mode;
    times are required. Returns ([], []) if the block is unusable."""
    times = hourly.get("time")
    codes = hourly.get("weather_code")
    precips = hourly.get("precipitation_probability")
    if not isinstance(times, list) or not isinstance(codes, list):
        return [], []
    picked_codes = []
    picked_precips = []
    for i, t in enumerate(times):
        if not isinstance(t, str) or len(t) < 13:
            continue
        hour = int(t[11:13])
        if not (DAYTIME_START_HOUR <= hour < DAYTIME_END_HOUR):
            continue
        if i >= len(codes) or codes[i] is None:
            continue
        picked_codes.append(codes[i])
        if isinstance(precips, list) and i < len(precips) and precips[i] is not None:
            picked_precips.append(precips[i])
    return picked_codes, picked_precips


def _first(daily, key):
    """First (today's) value of an Open-Meteo daily array, or None."""
    v = daily.get(key)
    if isinstance(v, list) and v:
        return v[0]
    return None


def parse_weather(raw_json):
    """raw_json: Open-Meteo response with `daily` (temps) and `hourly`
    (weather_code, precipitation_probability) blocks requested for a
    single day (forecast_days=1). Returns a small dict --
    {condition, tmax, tmin, precip} -- or None if the payload is unusable
    (so the caller keeps last-good / draws no weather). Temps are rounded
    to whole degrees (a glance display; the decimal is noise). condition
    is the daytime-mode bucket and precip is the daytime max probability
    in % (see module docstring for why daytime-only) -- None if the
    daytime window yields no samples."""
    if not raw_json:
        return None
    daily = raw_json.get("daily")
    hourly = raw_json.get("hourly")
    if not isinstance(daily, dict) or not isinstance(hourly, dict):
        return None
    tmax = _first(daily, "temperature_2m_max")
    tmin = _first(daily, "temperature_2m_min")
    if tmax is None or tmin is None:
        return None
    codes, precips = _daytime_hourly(hourly)
    if not codes:
        return None
    precip = max(precips) if precips else None
    return {
        "condition": dominant_condition(codes),
        "tmax": int(round(tmax)),
        "tmin": int(round(tmin)),
        "precip": None if precip is None else int(round(precip)),
        # The forecast's own local date ("YYYY-MM-DD" from Open-Meteo's
        # daily.time[0], returned regardless of which fields we request).
        # Lets the caller decide a kept last-good reading is still valid --
        # a daily high/low/condition a few hours old is still "today", but a
        # reading from a prior day is stale (see main.py's weather handling).
        # None only if the payload somehow omits time (Open-Meteo always sends
        # it); callers treat a missing/mismatched date as not-today.
        "date": _first(daily, "time"),
    }


def is_for_today(reading, today_iso):
    """True if a parsed reading (from parse_weather) is still TODAY's forecast
    -- its `date` matches `today_iso` (local 'YYYY-MM-DD'). A missing reading,
    or a missing/prior-day date, is not today -> False (covers the across-
    midnight case). This is the OUTER guard; keep_last_good adds a freshness
    bound on top (see below)."""
    return bool(reading) and reading.get("date") == today_iso


def keep_last_good(reading, today_iso, age_s, max_age_s):
    """Decide whether a failed/unusable weather pull should KEEP showing
    `reading` instead of falling back to the explicit "Weather error".

    True only when the reading is both still today's forecast (is_for_today)
    AND fresh enough -- fetched no more than `max_age_s` ago. `age_s` is
    now - fetched-at (main.py supplies it from the device clock); None means
    no prior good fetch -> not usable.

    The freshness bound matters because Open-Meteo REVISES the daily forecast
    through the day as new model runs arrive (precip probability especially --
    e.g. ICON re-runs every ~3h), so an unbounded "any time today" fallback
    could show a stale morning reading in the evening during a long outage.
    Within max_age_s the reading is recent enough to still be trustworthy;
    past it we'd rather show the honest error. Configurable via the weather
    block's max_age_min (see settings.example.json)."""
    if age_s is None:
        return False
    return is_for_today(reading, today_iso) and age_s <= max_age_s


def format_temps(weather):
    """High/low as one string, e.g. "6° / 12°" -- low first (the
    number that decides the jacket), matching the mockup."""
    return "%d° / %d°" % (weather["tmin"], weather["tmax"])


# Below this precipitation probability the cue is suppressed entirely (the
# condition icon already says "dry"); showing "5%" is noise on a glance
# display. Tuned to the design intent: surface rain only when it's a real
# possibility worth an umbrella. Lowered 20 -> 10 (2026-07-12): 20 was hiding
# numbers the owner still wanted to see.
PRECIP_SHOW_THRESHOLD = 10


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
