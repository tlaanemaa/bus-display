"""Thin I/O wrapper: fetch today's forecast JSON over HTTPS from
Open-Meteo (no API key, like SL). All parsing lives in weather.py so it
can be tested on host without a `requests` import (CLAUDE.md "Testability
rule"). Mirrors sl.py deliberately -- same retry/timeout shape.

Only today's daily fields are requested (forecast_days=1), keeping the
response ~1KB so the parse can't restart the RAM-vs-HTTPS fight the SL
fetch already had to win (see weather.py header)."""
import gc
import time
import requests

BASE_URL = "https://api.open-meteo.com/v1/forecast"
_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"


def fetch_today(latitude, longitude, retries=3, timeout_s=15):
    """Today's forecast for a lat/lon. timeout_s bounds each attempt like
    sl.fetch_departures (same intermittent-TLS-hang caveat -- main.py's
    watchdog is the real backstop). Returns the raw Open-Meteo dict;
    weather.parse_weather() turns it into the footer summary.

    timezone=auto so the daily min/max/precip aggregate over the LOCAL day
    at those coordinates (not UTC) -- otherwise "today's high" would be off
    for a chunk of the day."""
    url = "%s?latitude=%s&longitude=%s&daily=%s&timezone=auto&forecast_days=1" % (
        BASE_URL, latitude, longitude, _DAILY)
    last_err = None
    for attempt in range(retries):
        gc.collect()
        try:
            resp = requests.get(url, timeout=timeout_s)
            try:
                return resp.json()
            finally:
                resp.close()
        except Exception as e:
            last_err = e
            print("openmeteo: fetch attempt %d/%d failed: %s" % (attempt + 1, retries, e))
            time.sleep_ms(300)
    raise last_err
