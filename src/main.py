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

_WDT_FEED_CHUNK_S = 60   # feed the watchdog at least this often while idling between ticks, so a render
                          # interval longer than the WDT window can't trip a spurious reboot during a normal wait

_FB_WIDTH = 800
_FB_HEIGHT = 480


def _fetch_all_stops(cfg):
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


def _seconds_to_next_tick(interval_s):
    """Seconds to sleep so the next wake lands on the next wall-clock multiple
    of interval_s -- e.g. interval 60 wakes at the top of each minute
    (HH:MM:00), not 60s after boot, so the on-screen clock flips in step with
    a phone.

    Never wakes EARLY -- the property that matters, since waking before the
    rollover would render the old minute and leave the clock a full minute
    behind. int(time.time()) floors to whole seconds, so the computed sleep
    overshoots the true boundary by the current sub-second fraction: we land
    0..1s after HH:MM:00, never before it. (An earlier fixed +2s margin on
    top of this was removed 2026-07-10 as premature -- the floor alone already
    guarantees never-early, and a sub-second overshoot is imperceptible on a
    glance display while +2s visibly lagged the clock.) Uses the NTP-synced
    RTC; before NTP sync the epoch is arbitrary but ticks are still evenly
    spaced, so nothing breaks -- they just aren't aligned to real wall time
    until the clock is set."""
    return interval_s - (int(time.time()) % interval_s)


async def _sleep_until_next_tick(wdt, interval_s):
    """Await the next wall-clock-aligned tick (see _seconds_to_next_tick),
    feeding the watchdog every _WDT_FEED_CHUNK_S so a render interval longer
    than the WDT window doesn't trip a spurious reboot during a normal idle
    wait. The ESP32 is awake during asyncio.sleep either way, so chunking
    the wait to feed the WDT costs nothing."""
    remaining = _seconds_to_next_tick(interval_s)
    while remaining > 0:
        wdt.feed()
        chunk = remaining if remaining < _WDT_FEED_CHUNK_S else _WDT_FEED_CHUNK_S
        await asyncio.sleep(chunk)
        remaining -= chunk


def _local_now_strings():
    """(date_str, time_str) for the device's current local (Stockholm)
    time, computed from the NTP-synced UTC clock -- see localtime.py."""
    y, mo, d, h, mi, s, _weekday, _yday = time.gmtime()[:8]
    ly, lmo, ld, lh, lmi, _ls, _cest = localtime.utc_to_stockholm(y, mo, d, h, mi, s)
    return localtime.format_date(ly, lmo, ld), localtime.format_time(lh, lmi)


def _draw_and_refresh(epd, sections, footer, prev_sections, prev_footer, full):
    """Allocates the 48KB framebuffer only for this draw+push window, then
    lets it be freed (no reference survives the function returning) --
    see module docstring for why it must not stay resident. Exactly ONE
    48KB buffer is ever alive at a time, even for the differential partial
    path below (see CLAUDE.md RAM notes).

    `full` picks the refresh mode (see CLAUDE.md "Screen refresh strategy"
    for the tradeoff): a full refresh flashes black/white and fully
    discharges every pixel (clears accumulated ghosting); a partial refresh
    is near-instant with no flash. display_loop() decides the cadence.

    Partial refresh is a TRUE DIFFERENTIAL update (2026-07-10): the panel's
    partial mode drives each pixel from its 0x10 "old image" plane to its
    0x13 "new image" plane, so we must supply the actual previously-drawn
    frame (prev_sections/prev_footer) on 0x10, not just the new frame. That
    makes only genuinely-changed pixels move -> minimal ghosting, and it
    means the panel can be slept after every refresh (e-paper rule 1
    restored) because the differential no longer depends on controller RAM
    surviving between calls -- the old plane is re-uploaded explicitly. See
    epd7in5v2.py's partial_old()/partial_new(). One buffer serves both
    planes: render old -> stream to 0x10 -> re-render new into the SAME
    buffer -> stream to 0x13 (the 0x10 bytes already live in the panel
    controller by then)."""
    fb_buf = bytearray(_FB_WIDTH * _FB_HEIGHT // 8)
    fb = framebuf.FrameBuffer(fb_buf, _FB_WIDTH, _FB_HEIGHT, framebuf.MONO_HLSB)

    if full:
        display.draw_home(fb, sections, footer)
        epd.init()
        epd.display(fb_buf)
        epd.sleep()
        return

    # Differential partial: old plane (0x10) first, then new plane (0x13).
    epd.init_part()
    epd.partial_begin()
    display.draw_home(fb, prev_sections, prev_footer)  # OLD frame -> 0x10
    epd.partial_old(fb_buf)
    display.draw_home(fb, sections, footer)            # NEW frame -> 0x13 (same buffer; draw_home fill(0)s first)
    epd.partial_new(fb_buf)
    epd.sleep()


async def display_loop(cfg):
    """Long-lived task. Ticks once per render interval, each tick
    re-rendering from cached departures + the current clock and pushing a
    panel refresh only when the rendered text actually changed (e-paper
    rule 2). Fresh SL data is pulled on its own cadence, gated independently
    of the render tick (both default 1 min but separately tunable -- e.g.
    render every minute for a live clock while pulling data less often to be
    gentler on the API).

    Ticks are aligned to the WALL CLOCK, not to N-minutes-from-boot: the
    loop sleeps onto the next multiple of the render interval (see
    _seconds_to_next_tick), so a 1-min interval wakes at the top of each
    minute (HH:MM:00, within a sub-second) and the footer clock flips right
    as the real minute does. If a tick's work runs long (worst case a fetch
    exhausting all retries, ~90s), that tick simply lands on a later boundary
    and the next tick re-aligns -- it never drifts into N-min-from-last-wake.

    Three intervals, all in cfg IN MINUTES (see settings.example.json;
    converted to seconds below -- there's no reason to touch an e-ink panel
    more than ~1x/min):
      - data_pull_interval_min    how often to fetch fresh departures from SL
      - render_interval_min       tick cadence: re-render + refresh-if-changed
      - full_refresh_interval_min how often a push is a full (flashing)
                                  refresh vs a differential partial

    Refresh MODE (full vs. partial) -- see CLAUDE.md "Screen refresh
    strategy": most pushes use the near-instant, non-flashing DIFFERENTIAL
    partial mode, and a full (flashing) refresh is used at least every
    full_refresh_interval_s to clear residue. Partial refreshes need the
    previously-drawn frame (prev_sections/prev_footer) as their 0x10 "old
    image" plane, so it's cached after every refresh; the very first
    refresh is forced full (no previous frame exists yet). The panel is
    slept after EVERY refresh (e-paper rule 1) -- the differential
    re-uploads the old plane explicitly, so it doesn't depend on the panel
    staying powered between calls.

    Every configured stop is always shown (no primary/fallback anymore).
    Each stop's own last-good departures are kept independently, so one
    stop's fetch failure doesn't blank out another stop that's still
    fetching fine -- the footer's "(stale)" suffix is the only staleness
    signal (see display.footer_lines).
    """
    epd = EPD7in5V2()
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
    last_rendered = None
    last_full_refresh_ticks = None
    last_pull_bucket = None  # wall-clock // data_pull_interval_s of the last fetch
    # Config is in MINUTES (there's no reason to touch an e-ink panel more than
    # ~1x/min); converted to seconds here for the timing math.
    data_pull_interval_s = cfg.get("data_pull_interval_min", 1) * 60
    render_interval_s = cfg.get("render_interval_min", 1) * 60
    full_refresh_interval_s = cfg.get("full_refresh_interval_min", 30) * 60
    last_good = [[] for _ in cfg["stops"]]  # each stop's last-known departures
    stale = True
    prev_sections = None  # last-drawn frame, reused as the 0x10 old plane for the next partial
    prev_footer = None

    while True:
        wdt.feed()

        # Pull fresh departures on the wall-clock-aligned data_pull cadence,
        # independent of the render tick. The bucket is integer
        # wall-clock-seconds // interval, so a pull fires exactly once per
        # interval regardless of sub-second tick jitter (and aligns pulls to
        # the clock the same way the render tick is aligned).
        pull_bucket = int(time.time() // data_pull_interval_s)
        if pull_bucket != last_pull_bucket:
            try:
                results = _fetch_all_stops(cfg)
            except Exception as e:
                print("display_loop: unexpected fetch error:", e)
                results = [None] * len(cfg["stops"])

            all_ok = all(r is not None for r in results)
            for i, r in enumerate(results):
                if r is not None:
                    last_good[i] = r
            stale = not all_ok
            last_pull_bucket = pull_bucket

        if not any(last_good):
            await _sleep_until_next_tick(wdt, render_interval_s)
            continue

        sections = [display.stop_section(stop["name"], deps) for stop, deps in zip(cfg["stops"], last_good)]
        date_str, time_str = _local_now_strings()
        footer = display.footer_lines(date_str, time_str, stale=stale)
        flat = []
        for section in sections:
            flat.extend(display.section_lines(section))
        rendered_key = "\n".join(flat + footer)

        now = time.ticks_ms()
        full_due = (
            last_full_refresh_ticks is None
            or time.ticks_diff(now, last_full_refresh_ticks) >= full_refresh_interval_s * 1000
        )
        content_changed = rendered_key != last_rendered

        # Only refresh when content actually changed (e-paper rule 2). The
        # mode is full when a full refresh is due (or on the very first
        # refresh, which has no previous frame to differential against),
        # otherwise the non-flashing differential partial. Since we only
        # partial on a content change, ghosting only accumulates when we're
        # actually redrawing -- so gating the periodic full on content_changed
        # too is correct: nothing to clear if nothing has been redrawing.
        if content_changed:
            full = full_due or prev_sections is None
            print("display_loop: content changed, %s refresh" % ("full" if full else "partial"))
            _draw_and_refresh(epd, sections, footer, prev_sections, prev_footer, full=full)
            last_rendered = rendered_key
            prev_sections = sections
            prev_footer = footer
            if full:
                last_full_refresh_ticks = now
        gc.collect()

        # _sleep_until_next_tick feeds the WDT throughout the idle wait, so a
        # long fetch (~90s worst case) and the sleep are bounded separately,
        # not summed, against the 150s WDT window -- and any render interval is
        # safe even if it exceeds that window.
        await _sleep_until_next_tick(wdt, render_interval_s)


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

        # Warm the font advance caches on this still-clean heap, before the
        # fetch/render loop -- so no font state is allocated during a live
        # draw, where it would strand into the framebuffer region and
        # starve the next TLS handshake (see bitfont.py docstring).
        try:
            display.warm_fonts()
        except Exception as e:
            print("main: font warm failed (non-fatal):", e)

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
