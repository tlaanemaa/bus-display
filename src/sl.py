"""Thin I/O wrapper: fetch departures JSON over plain HTTP from SL's
Transport API (see AGENTS.md "SL Transport API" -- no API key needed; HTTP
not HTTPS on purpose, see BASE_URL). All parsing/filtering/formatting logic
lives in departures.py so it can be tested on host without a `requests`
import (see AGENTS.md "Testability rule").
"""
import gc
import requests

if False:
    from typing import Any

# Plain HTTP is deliberate: this is public, keyless transit data and the
# endpoint serves HTTP directly. It keeps this bounded adapter small on the
# PSRAM-less device; do not add an HTTPS transport without an explicit need.
BASE_URL = "http://transport.integration.sl.se/v1/sites/%s/departures"

def fetch_departures(
    site_id: "str | int",
    transport: str = "BUS",
    forecast: int = 60,
    direction: "int | None" = None,
    timeout_s: int = 10,
) -> "dict[str, Any]":
    """Fetch one validated SL payload.

    timeout_s bounds the request; main.py keeps a stale per-stop result on a
    failure and the next wake makes the next attempt. direction is SL's
    direction_code (1 or 2) to filter server-side, keeping
    the response small (see AGENTS.md "SL Transport API" -- keep the JSON
    small on-device). None means both directions.
    """
    url = "%s?transport=%s&forecast=%d" % (BASE_URL % site_id, transport, forecast)
    if direction is not None:
        url += "&direction=%d" % direction
    gc.collect()
    response = requests.get(url, timeout=timeout_s)
    try:
        status = getattr(response, "status_code", 200)
        if status < 200 or status >= 300:
            raise OSError("HTTP %d" % status)
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("departures"), list):
            raise ValueError("response missing valid departures list")
        return payload
    finally:
        response.close()
