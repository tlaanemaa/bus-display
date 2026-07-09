"""Thin I/O wrapper: fetch departures JSON over HTTPS from SL's Transport
API (see CLAUDE.md "SL Transport API" -- no API key needed). All
parsing/filtering/formatting logic lives in departures.py so it can be
tested on host without a `requests` import (see CLAUDE.md "Testability
rule").
"""
import gc
import time
import requests

BASE_URL = "https://transport.integration.sl.se/v1/sites/%s/departures"


def fetch_departures(site_id, transport="BUS", forecast=60, direction=None, retries=3, timeout_s=15):
    """timeout_s bounds each attempt so a stuck TLS handshake (observed
    intermittently right after a fresh Wi-Fi connect -- see CLAUDE.md
    "Departures logic & stops") usually fails like any other network error
    instead of hanging, letting the retry/stale-data fallback take over.
    NOT a complete guarantee, though -- confirmed some hangs happen inside
    blocking crypto work this timeout doesn't cover; main.py's hardware
    watchdog is the actual backstop against those.

    direction: SL's direction_code (1 or 2) to filter server-side, keeping
    the response small (see CLAUDE.md "SL Transport API" -- keep the JSON
    small on-device). None means both directions.
    """
    url = "%s?transport=%s&forecast=%d" % (BASE_URL % site_id, transport, forecast)
    if direction is not None:
        url += "&direction=%d" % direction
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
            print("sl: fetch attempt %d/%d failed: %s" % (attempt + 1, retries, e))
            time.sleep_ms(300)
    raise last_err
