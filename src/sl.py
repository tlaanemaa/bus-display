"""Thin I/O wrapper: fetch departures JSON over plain HTTP from SL's
Transport API (see CLAUDE.md "SL Transport API" -- no API key needed; HTTP
not HTTPS on purpose, see BASE_URL). All parsing/filtering/formatting logic
lives in departures.py so it can be tested on host without a `requests`
import (see CLAUDE.md "Testability rule").
"""
import gc
import time
import requests

# Plain HTTP, deliberately: SL serves this endpoint over http with no
# redirect to https (verified 2026-07-12), and doing so SKIPS THE TLS
# HANDSHAKE ENTIRELY -- which is the whole RAM-vs-HTTPS conflict on this
# PSRAM-less board (mbedtls's RSA-2048 cert verification intermittently
# ran out of contiguous heap and hung/failed the fetch; see CLAUDE.md).
# The data is public transit times with no key or credentials, so there's
# nothing to protect by encrypting it.
BASE_URL = "http://transport.integration.sl.se/v1/sites/%s/departures"

# Same retry/timeout shape as openmeteo.py -- kept in sync deliberately (see
# its module docstring). timeout_s=10 is deliberately aggressive: a hung
# request past 10s is treated as dead and retried rather than waited out.
# RETRY_DELAY_S=3 (not longer) keeps 2 stops x 3 retries within main.py's
# per-tick WDT budget -- see main.py's WDT_TIMEOUT_MS comment for the math.
RETRY_DELAY_S = 3


_CHUNK = 512


def _stream_departures(resp):
    """Read the body off the socket in small chunks and reduce it one
    departure at a time (departures.DepartureSplitter) -- never holding the
    whole reply or its full object tree. See departures.py for why: the
    whole-reply json parse's RAM peak starved the Wi-Fi stack. Returns the
    same {"departures": [...]} shape parse_departures() takes."""
    import json
    import departures as dep
    splitter = dep.DepartureSplitter()
    out = []
    length = None
    try:
        length = int(resp.headers.get("Content-Length")) if resp.headers else None
    except Exception:
        pass
    got = 0
    raw = resp.raw
    while length is None or got < length:
        want = _CHUNK if length is None else min(_CHUNK, length - got)
        chunk = raw.read(want)
        if not chunk:
            break
        got += len(chunk)
        for obj in splitter.feed(chunk):
            out.append(dep.compact_departure(json.loads(obj)))
    if length is not None and got < length:
        raise OSError("short read %d/%d" % (got, length))
    if not splitter.found_key:
        raise ValueError("response has no 'departures'")
    return {"departures": out}


def fetch_departures(site_id, transport="BUS", forecast=60, direction=None, retries=3, timeout_s=10):
    """timeout_s bounds each attempt so a stuck request (observed
    intermittently right after a fresh Wi-Fi connect -- see CLAUDE.md
    "Departures logic & stops") usually fails like any other network error
    instead of hanging, letting the retry/stale-data fallback take over.
    NOT a complete guarantee, though -- confirmed some hangs happen inside
    blocking socket work this timeout doesn't cover; main.py's hardware
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
                # A non-200 (SL hiccup, CloudFront 5xx, captive page) or a body
                # without "departures" is a FAILED fetch, not "zero buses".
                # Before this check such a reply parsed to [] and silently
                # replaced the good cache with "No departures" and NO stale
                # badge -- one of the "buses go missing" causes.
                if resp.status_code != 200:
                    raise OSError("HTTP %d" % resp.status_code)
                return _stream_departures(resp)
            finally:
                resp.close()
        except Exception as e:
            last_err = e
            print("sl: fetch attempt %d/%d failed: %s" % (attempt + 1, retries, e))
            # 429 = SL's rate limit. Retrying 3 s later just got another 429
            # (seen on hardware 2026-11-08) and spends more of the quota, so
            # give up for this pull; the cache keeps counting down locally and
            # one missed pull is far below the STALE threshold.
            if "HTTP 429" in str(e):
                break
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY_S)
    raise last_err
