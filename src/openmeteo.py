"""Thin I/O wrapper: fetch today's forecast JSON over plain HTTP from
Open-Meteo (no API key, like SL; HTTP not HTTPS -- see BASE_URL). All
parsing lives in weather.py so it
can be tested on host without a `requests` import (AGENTS.md "Source layout
and implementation rules"). Mirrors sl.py deliberately -- one bounded request
per call.

Only today's daily + hourly fields are requested (forecast_days=1, so 24
hourly rows), keeping the response small on the PSRAM-less device."""
import gc
import requests

if False:
    from typing import Any

# Plain HTTP, like sl.py: this is public, keyless weather data and the endpoint
# serves HTTP directly. It avoids an HTTPS TLS handshake's large contiguous-
# memory demand on this no-PSRAM device. Keep the adapter bounded and small.
BASE_URL = "http://api.open-meteo.com/v1/forecast"
# weather_code and precip move to hourly (see weather.py header) so the
# condition glyph can be a daytime-mode instead of Open-Meteo's daily
# aggregate, which is the WORST hourly code of the full 24h day -- it was
# reporting "cloudy" on days that were clear whenever people were actually
# awake. temperature_2m_max/min stay on the daily block (unaffected).
_DAILY = "temperature_2m_max,temperature_2m_min"
_HOURLY = "weather_code,precipitation_probability"

def fetch_today(
    latitude: "str | float",
    longitude: "str | float",
    timeout_s: int = 10,
) -> "dict[str, Any]":
    """Fetch one validated forecast for a lat/lon.

    timeout_s bounds the request; main.py keeps a fresh-enough last-good
    forecast on failure and makes at most one new attempt when its next
    scheduled pull is due. Returns the raw Open-Meteo dict;
    weather.parse_weather() turns it into the footer summary.

    timezone=auto so the daily min/max and hourly timestamps aggregate over
    the LOCAL day at those coordinates (not UTC) -- otherwise "today's high"
    (and the daytime-hour filtering weather.py does on the hourly block)
    would be off for a chunk of the day."""
    url = "%s?latitude=%s&longitude=%s&daily=%s&hourly=%s&timezone=auto&forecast_days=1" % (
        BASE_URL, latitude, longitude, _DAILY, _HOURLY)
    gc.collect()
    response = requests.get(url, timeout=timeout_s)
    try:
        status = getattr(response, "status_code", 200)
        if status < 200 or status >= 300:
            raise OSError("HTTP %d" % status)
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("daily"), dict)
            or not isinstance(payload.get("hourly"), dict)
        ):
            raise ValueError("response missing valid daily/hourly data")
        return payload
    finally:
        response.close()
