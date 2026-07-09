"""Entry point: boot flow, asyncio loop.

Boot flow (see CLAUDE.md "Architecture"): load /config.json (Wi-Fi creds
only) -> try Wi-Fi STA -> on success, NTP sync, then load /settings.json
(stop ids, direction filter, refresh cadence -- see settings.py) and go
straight into the departures home display; on failure, start the AP-mode
setup portal instead.

/settings.json is NOT committed to git (see .gitignore) so the owner's
home stop doesn't end up in a public repo -- copy src/settings.example.json
to src/settings.json, fill in your stop(s) (see CLAUDE.md "Departures
logic & stops" for how to find a site id), then deploy it like any other
file: `mpremote connect COM3 fs cp src/settings.json :settings.json`.

The 48KB framebuffer is deliberately NOT allocated once at the top and
kept resident -- see CLAUDE.md "Departures logic & stops" / RAM notes.
Measured on this board: with it resident, the SL HTTPS/TLS handshake
fails or hangs every time, even with 75-90KB nominally free (mbedtls's
RSA-2048 certificate handling for this host needs more contiguous room
than that, and repeated gc.collect() doesn't recover it -- it's live
object memory, not garbage). Freeing the framebuffer during the fetch and
only allocating it for the brief draw+refresh window fixes this
reliably. The admin server (Microdot) is also not even imported once
connected, for the same reason -- `server.py` builds its whole app and
route table at import time (costing the same ~30KB+) regardless of
whether `start_server()` is ever called, so `import server` is deferred
into the AP-mode branch below where it's actually needed. It only has the
Wi-Fi setup form today anyway, which is moot once already connected.
"""
import framebuf
import network
import asyncio
import ntptime
import time
import gc
import sys
import machine

import config
import settings
import wifi
import sl
import departures
import display
import localtime
from epd7in5v2 import EPD7in5V2

# `server` (Microdot) is deliberately NOT imported here -- server.py builds
# its whole Microdot app + route table at import time, which costs the same
# ~30KB+ that starving the HTTPS fetch. It's only ever needed in AP-mode
# setup, so it's imported lazily inside that branch of main() below. Simply
# having "import server" at module level here was reintroducing the exact
# RAM-vs-TLS conflict documented in CLAUDE.md ("Departures logic & stops")
# even though start_server() was never called in STA mode -- confirmed by
# isolating the import as the difference between a script that fetched
# reliably and the real main.py hanging on every single fresh boot.

WDT_TIMEOUT_MS = 150000     # hardware watchdog: force a reboot if one display_loop iteration ever takes
                             # longer than this. Needed because the intermittent TLS hang documented in
                             # CLAUDE.md ("Departures logic & stops") isn't reliably bounded by sl.py's own
                             # socket-level timeout -- it can happen inside the handshake's own blocking
                             # crypto work, not a socket read, so no Python-level timeout can interrupt it.
                             # 150s gives headroom over the worst legitimate case with 2 configured stops
                             # (2 stops x 3 retries x 15s timeout each = ~92s) -- if settings.json ever lists
                             # more than ~3 stops, this may need raising.

_FB_WIDTH = 800
_FB_HEIGHT = 480


async def _fetch_all_stops(cfg):
    """Fetch every configured stop independently, in the order given in
    settings.json -- no primary/fallback/suitability logic anymore, just
    an ordered list of stops the owner cares about (see CLAUDE.md
    "Departures logic & stops"). Returns a list, one entry per stop: the
    stop's next cfg["departures_per_stop"] departures, or None if this
    stop's fetch failed this cycle (caller falls back to that stop's own
    cached data)."""
    results = []
    for stop in cfg["stops"]:
        try:
            raw = sl.fetch_departures(stop["site_id"], forecast=cfg["forecast_min"], direction=cfg["direction_code"])
            deps = departures.parse_departures(raw)
            results.append(deps[:cfg["departures_per_stop"]])
        except Exception as e:
            print("fetch: stop %s failed: %s" % (stop["name"], e))
            results.append(None)
    return results


def _local_now_strings():
    """(date_str, time_str) for the device's current local (Stockholm)
    time, computed from the NTP-synced UTC clock -- see localtime.py."""
    y, mo, d, h, mi, s, _weekday, _yday = time.gmtime()[:8]
    ly, lmo, ld, lh, lmi, _ls, _cest = localtime.utc_to_stockholm(y, mo, d, h, mi, s)
    return localtime.format_date(ly, lmo, ld), localtime.format_time(lh, lmi)


def _draw_and_refresh(epd, sections, footer):
    """Allocates the 48KB framebuffer only for this draw+push window, then
    lets it be freed (no reference survives the function returning) --
    see module docstring for why it must not stay resident."""
    fb_buf = bytearray(_FB_WIDTH * _FB_HEIGHT // 8)
    fb = framebuf.FrameBuffer(fb_buf, _FB_WIDTH, _FB_HEIGHT, framebuf.MONO_HLSB)
    display.draw_home(fb, sections, footer)

    epd.init()
    epd.display(fb_buf)
    epd.sleep()


async def display_loop(cfg):
    """Long-lived task: poll SL every cfg["poll_interval_s"], but only push
    an actual panel refresh when the rendered text changed AND at least
    cfg["min_refresh_interval_s"] has passed since the last one (e-paper
    rules 1-2 in CLAUDE.md -- full refresh only when content changed, and
    not more often than the panel can tolerate). The footer's current-time
    line changes every minute, which naturally forces a refresh at that
    cadence per the owner's request -- see CLAUDE.md "Departures logic &
    stops".

    Every configured stop is always shown (no primary/fallback anymore).
    Each stop's own last-good departures are kept independently, so one
    stop's fetch failure doesn't blank out another stop that's still
    fetching fine -- "updated" only advances when ALL stops succeed in
    the same cycle, so a partial failure is still visible as stale.
    """
    epd = EPD7in5V2()
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
    last_rendered = None
    last_refresh_ticks = None
    last_good = [[] for _ in cfg["stops"]]  # each stop's last-known departures
    updated_str = "--:--"  # last-updated local time, only advances when every stop succeeds

    while True:
        wdt.feed()
        try:
            results = await _fetch_all_stops(cfg)
        except Exception as e:
            print("display_loop: unexpected fetch error:", e)
            results = [None] * len(cfg["stops"])

        all_ok = all(r is not None for r in results)
        for i, r in enumerate(results):
            if r is not None:
                last_good[i] = r
        stale = not all_ok

        if not any(last_good):
            await asyncio.sleep(cfg["poll_interval_s"])
            continue

        if all_ok:
            _, updated_str = _local_now_strings()

        sections = [display.stop_section(stop["name"], deps) for stop, deps in zip(cfg["stops"], last_good)]
        date_str, time_str = _local_now_strings()
        footer = display.footer_lines(updated_str, date_str, time_str, stale=stale)
        flat = []
        for section in sections:
            flat.extend(display.section_lines(section))
        rendered_key = "\n".join(flat + footer)

        now = time.ticks_ms()
        due_for_refresh = (
            last_refresh_ticks is None
            or time.ticks_diff(now, last_refresh_ticks) >= cfg["min_refresh_interval_s"] * 1000
        )
        if rendered_key != last_rendered and due_for_refresh:
            print("display_loop: content changed, refreshing panel")
            _draw_and_refresh(epd, sections, footer)
            gc.collect()
            last_rendered = rendered_key
            last_refresh_ticks = now
        elif rendered_key != last_rendered:
            print("display_loop: content changed but min_refresh_interval_s not elapsed, skipping panel write")

        await asyncio.sleep(cfg["poll_interval_s"])


async def main():
    wifi_cfg = config.load().get("wifi")

    connected = False
    if wifi_cfg and wifi_cfg.get("ssid"):
        connected = wifi.connect_sta(wifi_cfg["ssid"], wifi_cfg.get("password", ""))

    if connected:
        try:
            ntptime.settime()
            print("main: NTP sync ok")
        except Exception as e:
            print("main: NTP sync failed:", e)

        ip = network.WLAN(network.STA_IF).ifconfig()[0]
        print("main: connected, ip =", ip)

        # A short settle delay here measurably improved first-fetch
        # reliability in testing (4/4 clean vs. intermittent hangs/crashes
        # without it) -- something about the Wi-Fi/TLS stack right after a
        # fresh connect needs a moment before the first HTTPS handshake.
        # See CLAUDE.md "Departures logic & stops".
        await asyncio.sleep_ms(3000)

        await display_loop(settings.load())
    else:
        wifi.start_ap()
        print("main: no/failed Wi-Fi config -- setup form at http://192.168.4.1")
        print("main: starting admin server on port 80")
        import server
        await server.app.start_server(port=80)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    raise
except Exception as e:
    print("main: fatal error, idling for recovery (Ctrl-C for REPL):")
    sys.print_exception(e)
    while True:
        time.sleep(1)
