"""Thin I/O wrapper: fetch today's forecast JSON over plain HTTP from
Open-Meteo (no API key, like SL; HTTP not HTTPS -- see BASE_URL). All
parsing lives in weather.py so it
can be tested on host without a `requests` import (CLAUDE.md "Testability
rule"). Mirrors sl.py deliberately -- same retry/timeout shape.

Only today's daily + hourly fields are requested (forecast_days=1, so 24
hourly rows), keeping the response a couple KB so the parse can't restart
the RAM-vs-HTTPS fight the SL fetch already had to win (see weather.py
header)."""
import gc
import time
import requests

# Plain HTTP, like sl.py (verified http with no https redirect 2026-07-12):
# skipping the TLS handshake avoids the mbedtls contiguous-RAM starvation
# that plagues this board (see CLAUDE.md "RAM-vs-HTTPS conflict"). Public
# weather data, no key -- nothing to protect.
BASE_URL = "http://api.open-meteo.com/v1/forecast"
# weather_code and precip move to hourly (see weather.py header) so the
# condition glyph can be a daytime-mode instead of Open-Meteo's daily
# aggregate, which is the WORST hourly code of the full 24h day -- it was
# reporting "cloudy" on days that were clear whenever people were actually
# awake. temperature_2m_max/min stay on the daily block (unaffected).
_DAILY = "temperature_2m_max,temperature_2m_min"
_HOURLY = "weather_code,precipitation_probability"

# Same retry/timeout shape as sl.py -- kept in sync deliberately (see this
# module's docstring). See sl.RETRY_DELAY_S for the budget reasoning.
RETRY_DELAY_S = 3


def fetch_today(latitude, longitude, retries=3, timeout_s=10):
    """Today's forecast for a lat/lon. timeout_s bounds each attempt like
    sl.fetch_departures (same intermittent-hang caveat -- main.py's watchdog
    is the real backstop). Returns the raw Open-Meteo dict;
    weather.parse_weather() turns it into the footer summary.

    timezone=auto so the daily min/max and hourly timestamps aggregate over
    the LOCAL day at those coordinates (not UTC) -- otherwise "today's high"
    (and the daytime-hour filtering weather.py does on the hourly block)
    would be off for a chunk of the day."""
    url = "%s?latitude=%s&longitude=%s&daily=%s&hourly=%s&timezone=auto&forecast_days=1" % (
        BASE_URL, latitude, longitude, _DAILY, _HOURLY)
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
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY_S)
    raise last_err
