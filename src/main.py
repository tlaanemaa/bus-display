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

The 48KB framebuffer is allocated ONCE at boot (in display_loop) and kept
resident, reused for every refresh -- see CLAUDE.md "RAM-vs-HTTPS conflict
(RESOLVED)". This reverses an earlier design: the framebuffer used to be
allocated transiently, per cycle, ONLY because a resident buffer starved
the SL TLS handshake (mbedtls's RSA-2048 cert verification needs a large
contiguous block). Now that SL and Open-Meteo are fetched over plain HTTP
(no TLS handshake at all), that pressure is gone -- and a resident buffer
is also more robust, since a fresh 48KB alloc had begun to MemoryError on
later cycles as the heap fragmented (MicroPython's GC never compacts).

The admin server (Microdot) is still not imported once connected --
`server.py` builds its whole app + route table at import time (~30KB+)
regardless of whether start_server() is ever called, and an unused import
whose top-level code does real work isn't free. It's deferred into the
AP-mode branch below where it's actually needed (Wi-Fi setup only).
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
import openmeteo
import weather
from epd7in5v2 import EPD7in5V2

# `server` (Microdot) is deliberately NOT imported here -- server.py builds
# its whole Microdot app + route table at import time (~30KB+ resident). It's
# only ever needed in AP-mode setup, so it's imported lazily inside that
# branch of main() below. General lesson (it historically also starved the
# TLS fetch, now moot with HTTP): an unused import isn't free if its
# top-level code does real work -- confirmed by isolating the import as the
# difference between a script that fetched
# reliably and the real main.py hanging on every single fresh boot.

WDT_TIMEOUT_MS = 150000     # hardware watchdog: force a reboot if one display_loop iteration ever takes
                             # longer than this -- a general hang backstop. The TLS hang it was originally
                             # for is now moot (fetches are plain HTTP, see CLAUDE.md "RAM-vs-HTTPS conflict
                             # (RESOLVED)"), so this should rarely fire, but a stuck socket read or driver
                             # busy-wait could still hang an iteration. 150s gives headroom over the worst
                             # legitimate case with 2 configured stops: one fetch's worst case is
                             # retries*timeout_s + (retries-1)*RETRY_DELAY_S = 3*10 + 2*3 = 36s (sl.py /
                             # openmeteo.py share this shape), so 2 stops + weather sequentially worst-cases
                             # at ~108s. Note weather now retries EVERY tick while erroring (see
                             # display_loop's weather_error handling), not just its own slow bucket, so that
                             # 36s is a common addition during an outage, not a rare coincidence -- still
                             # well inside the 150s budget. If settings.json ever lists more than ~3 stops,
                             # this may need raising.

_WDT_FEED_CHUNK_S = 60   # feed the watchdog at least this often while idling between ticks, so a render
                          # interval longer than the WDT window can't trip a spurious reboot during a normal wait

_FB_WIDTH = 800
_FB_HEIGHT = 480

# After this many consecutive pulls in which EVERY stop's fetch failed, attempt
# an explicit Wi-Fi reconnect (see wifi.reconnect). All stops failing at once is
# a connectivity signal, not an SL-side one. 3 (~3 min at the default 1-min pull)
# gives the ESP32's own auto-reconnect a chance to recover first before we step in.
_WIFI_RECONNECT_AFTER_FAILS = 3


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


def _local_today_iso():
    """Current local (Stockholm) date as 'YYYY-MM-DD' -- the same format as
    Open-Meteo's forecast date (weather.parse_weather's 'date'), so a kept
    last-good weather reading can be checked for being still today's. Assumes
    the weather coords share the device's timezone (true here: both Stockholm);
    a far-away weather location could disagree by a day near midnight, which
    would just surface the honest 'Weather error' a bit early."""
    y, mo, d, h, mi, s, _weekday, _yday = time.gmtime()[:8]
    ly, lmo, ld, _lh, _lmi, _ls, _cest = localtime.utc_to_stockholm(y, mo, d, h, mi, s)
    return "%04d-%02d-%02d" % (ly, lmo, ld)


def _safe_sleep(epd):
    """Best-effort panel power-down, called from a finally so a mid-refresh
    error never leaves the panel powered/active between cycles (e-paper rule
    1 -- leaving it active degrades it). Swallows its own error so it can't
    mask the original refresh exception; a hang inside sleep() itself is the
    hardware watchdog's job, not this function's."""
    try:
        epd.sleep()
    except Exception as e:
        print("display: panel sleep after a refresh error also failed:", e)


def _draw_and_refresh(epd, fb, fb_buf, frame, prev_frame, full):
    """Draws `frame` into the RESIDENT framebuffer (fb/fb_buf, allocated once
    at boot and passed in -- see display_loop) and pushes it to the panel.

    The framebuffer used to be allocated transiently, per cycle, ONLY because
    a resident 48KB buffer starved the SL TLS handshake (CLAUDE.md "RAM-vs-
    HTTPS conflict"). Now that SL and Open-Meteo are fetched over plain HTTP,
    nothing does a TLS handshake, so no big contiguous block is contended --
    a single resident buffer is both safe and BETTER: a fresh 48KB alloc had
    started failing (MemoryError) on later cycles once fetches became reliable,
    because the heap fragments and MicroPython's GC never compacts. Allocating
    once, when the heap is cleanest, sidesteps that entirely.

    `frame` is the screen content -- the tuple splatted into draw_home()
    (sections, footer, weather). `prev_frame` is the previously-drawn frame,
    needed as the differential partial's 0x10 old plane.

    `full` picks the refresh mode (see CLAUDE.md "Screen refresh strategy"):
    a full refresh flashes black/white and fully discharges every pixel
    (clears ghosting); a partial refresh is near-instant with no flash.

    Partial refresh is a TRUE DIFFERENTIAL update (2026-07-10): the panel
    drives each pixel from its 0x10 "old image" plane to its 0x13 "new image"
    plane, so we supply the actual previously-drawn frame on 0x10, not just
    the new frame -> only genuinely-changed pixels move (minimal ghosting),
    and the panel can be slept after every refresh (the old plane is
    re-uploaded explicitly). See epd7in5v2.py's partial_old()/partial_new().
    The one buffer serves both planes: render old -> stream to 0x10 ->
    re-render new into the SAME buffer -> stream to 0x13."""
    # draw_home() for the full path is pure framebuffer work and runs BEFORE
    # the panel is powered, so it needs no sleep guard; from epd.init() onward
    # the panel is powered, so everything past it is wrapped to guarantee a
    # power-down (see _safe_sleep) even if a write/busy-wait throws mid-refresh.
    if full:
        display.draw_home(fb, *frame)
        try:
            epd.init()
            epd.display(fb_buf)
        finally:
            _safe_sleep(epd)
        return

    # Differential partial: old plane (0x10) first, then new plane (0x13).
    # init_part() powers the panel, so the whole sequence (including the
    # draw_home re-renders between planes) is inside the try -> always slept.
    try:
        epd.init_part()
        epd.partial_begin()
        display.draw_home(fb, *prev_frame)   # OLD frame -> 0x10
        epd.partial_old(fb_buf)
        display.draw_home(fb, *frame)        # NEW frame -> 0x13 (same buffer; draw_home fill(0)s first)
        epd.partial_new(fb_buf)
    finally:
        _safe_sleep(epd)


async def display_loop(cfg, wifi_cfg=None):
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
    previously-drawn frame (`prev_frame`) as their 0x10 "old image" plane, so
    it's cached after every refresh; the very first refresh is forced full
    (no previous frame exists yet). The panel is
    slept after EVERY refresh (e-paper rule 1) -- the differential
    re-uploads the old plane explicitly, so it doesn't depend on the panel
    staying powered between calls.

    Every configured stop is always shown (no primary/fallback anymore).
    Each stop's own last-good departures are kept independently, so one
    stop's fetch failure doesn't blank out another stop that's still
    fetching fine -- the failed stop gets a per-stop STALE badge (see
    display.draw_home / stale_flags), the others are untouched.
    """
    epd = EPD7in5V2()
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
    # Resident framebuffer: allocated ONCE here (the heap is at its cleanest,
    # ~90KB contiguous) and reused for every refresh. Safe now that nothing
    # does TLS (SL + Open-Meteo are plain HTTP) and more robust than the old
    # per-cycle alloc, which had begun to MemoryError as the heap fragmented.
    # See _draw_and_refresh.
    gc.collect()
    fb_buf = bytearray(_FB_WIDTH * _FB_HEIGHT // 8)
    fb = framebuf.FrameBuffer(fb_buf, _FB_WIDTH, _FB_HEIGHT, framebuf.MONO_HLSB)
    last_rendered = None
    last_full_refresh_ticks = None
    last_pull_bucket = None  # wall-clock // data_pull_interval_s of the last fetch
    # Config is in MINUTES (there's no reason to touch an e-ink panel more than
    # ~1x/min); converted to seconds here for the timing math.
    data_pull_interval_s = cfg.get("data_pull_interval_min", 1) * 60
    render_interval_s = cfg.get("render_interval_min", 1) * 60
    full_refresh_interval_s = cfg.get("full_refresh_interval_min", 30) * 60
    last_good = [[] for _ in cfg["stops"]]     # each stop's last-known departures
    stale_flags = [False] * len(cfg["stops"])  # per-stop: is this stop showing OLD data (last fetch failed)?
    have_fetched = False  # has ANY pull attempt completed yet? (distinct from "the data is empty")
    consecutive_all_failed = 0  # pulls in a row where EVERY stop errored -> Wi-Fi reconnect trigger
    prev_frame = None     # last-drawn (sections, footer, weather); the 0x10 old plane for the next partial

    # Re-sync the RTC from NTP on a slow (daily) wall-clock bucket. The clock
    # is set once at boot (main()), but the ESP32 RTC drifts over long 24/7
    # uptime, and a FAILED boot sync would otherwise leave the footer clock
    # wrong forever. last_ntp_bucket starts None so the first tick resyncs
    # (harmlessly redundant after a good boot sync, self-healing after a bad
    # one); failures are caught and simply retried on the next tick.
    ntp_resync_interval_s = 24 * 3600
    last_ntp_bucket = None

    # Optional today-weather footer (see CLAUDE.md "Screen design"). Absent
    # or disabled -> the footer draws the clock only, exactly as before.
    # Pulled on its own slow cadence (weather changes slowly; be gentle on
    # the keyless Open-Meteo quota), independent of the departures pull.
    weather_cfg = cfg.get("weather")
    weather_enabled = bool(weather_cfg and weather_cfg.get("enabled", True)
                           and weather_cfg.get("latitude") is not None
                           and weather_cfg.get("longitude") is not None)
    weather_pull_interval_s = (weather_cfg.get("pull_interval_min", 30) if weather_cfg else 30) * 60
    last_weather_bucket = None
    last_weather = None   # last-good weather summary (kept for logging even while erroring)
    weather_error = False  # this pull failed/unusable -> render WEATHER_ERROR, not the stale reading

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

            for i, r in enumerate(results):
                if r is not None:
                    last_good[i] = r
            # The STALE badge tracks whether THIS stop's fetch errored, full
            # stop -- an error means what's on screen (old data, or "No
            # departures") can't be trusted. Not gated on having prior data:
            # "No departures" + STALE says "couldn't fetch", which is exactly
            # the distinction that matters.
            stale_flags = [r is None for r in results]
            last_pull_bucket = pull_bucket
            have_fetched = True

            # Every stop failing at once points at connectivity (Wi-Fi/router),
            # not one stop's SL data. After a few such pulls in a row, force a
            # reconnect -- the ESP32 usually self-heals, but a router power-cycle
            # is the likeliest 24/7 outage and auto-reconnect isn't guaranteed.
            # Counter resets on any success or after an attempt, so we never
            # hammer: at most one reconnect per _WIFI_RECONNECT_AFTER_FAILS pulls.
            if all(stale_flags):
                consecutive_all_failed += 1
            else:
                consecutive_all_failed = 0
            if (consecutive_all_failed >= _WIFI_RECONNECT_AFTER_FAILS
                    and wifi_cfg and wifi_cfg.get("ssid")):
                try:
                    wifi.reconnect(wifi_cfg["ssid"], wifi_cfg.get("password", ""))
                except Exception as e:
                    print("display_loop: Wi-Fi reconnect attempt failed:", e)
                consecutive_all_failed = 0

        # Re-sync the RTC from NTP on the slow daily bucket (see above). Kept
        # inside the loop, not just at boot, so long-uptime drift and a failed
        # boot sync both self-heal. On failure we DON'T advance the bucket, so
        # it retries every tick until it succeeds (bounded by ntptime's own
        # ~1s socket timeout; NTP outages are rare) -- same retry-until-good
        # shape as the weather_error path below.
        ntp_bucket = int(time.time() // ntp_resync_interval_s)
        if ntp_bucket != last_ntp_bucket:
            try:
                ntptime.settime()
                last_ntp_bucket = ntp_bucket
                print("display_loop: NTP resync ok")
            except Exception as e:
                print("display_loop: NTP resync failed:", e)

        # Weather on its own (much slower) wall-clock-aligned bucket.
        #
        # On a fetch failure or unusable payload we DON'T immediately show
        # "Weather error" -- weather is a DAILY forecast (today's high/low/
        # condition), so a last-good reading that's still for today is a few
        # hours old at worst and perfectly usable (owner's call: a slightly
        # aged reading beats an error). We only fall back to the explicit
        # error when there's nothing valid to show -- no last-good at all, or
        # a last-good from a PRIOR day (e.g. across midnight during an
        # outage), which would be genuinely stale. `date` on the parsed
        # reading (weather.parse_weather) vs. _local_today_iso() is that test;
        # it subsumes both "wrong date" and "too old" (anything older than
        # today no longer matches).
        #
        # The bucket is re-checked every tick ONLY while weather_error is set
        # (i.e. while we're actually showing the error) -- then we retry
        # eagerly to clear it fast. While we're happily showing a valid
        # last-good reading, weather_error is False, so we just wait out the
        # normal (up to 30 min) bucket; there's no urgency, and a next-bucket
        # refetch will refresh it. Uses openmeteo.fetch_today's default
        # retries/timeout (see WDT_TIMEOUT_MS for the worst-case-time math).
        if weather_enabled:
            weather_bucket = int(time.time() // weather_pull_interval_s)
            if weather_bucket != last_weather_bucket or weather_error:
                fetched = None
                try:
                    raw = openmeteo.fetch_today(weather_cfg["latitude"], weather_cfg["longitude"])
                    fetched = weather.parse_weather(raw)
                    if fetched is None:
                        print("weather: unusable payload")
                except Exception as e:
                    print("weather: fetch failed:", e)
                if fetched is not None:
                    last_weather = fetched
                    weather_error = False
                    print("weather: " + weather.summary_text(fetched))
                elif weather.is_for_today(last_weather, _local_today_iso()):
                    # Keep the last-good reading: it's still today's forecast.
                    weather_error = False
                    print("weather: fetch failed -- keeping today's last-good ("
                          + weather.summary_text(last_weather) + ")")
                else:
                    # Nothing valid for today -> honest error, retry each tick.
                    weather_error = True
                    print("weather: no valid reading for today -- showing error")
                last_weather_bucket = weather_bucket

        # Skip rendering ONLY before the very first pull attempt completes --
        # tracked by have_fetched, NOT inferred from "last_good is empty".
        # A successful pull that legitimately returns zero departures (a normal
        # nighttime state for a sparse stop) also leaves last_good empty, and
        # that case MUST fall through to render "No departures" -- otherwise the
        # panel would keep silently displaying the last evening's departures
        # with no STALE badge (the exact stale-mistaken-for-current failure the
        # badges exist to prevent). Once have_fetched is set, every subsequent
        # tick renders: real data, "No departures", or STALE, as applicable.
        if not have_fetched:
            await _sleep_until_next_tick(wdt, render_interval_s)
            continue

        sections = [display.stop_section(stop["name"], deps, stale=sf)
                    for stop, deps, sf in zip(cfg["stops"], last_good, stale_flags)]
        date_str, time_str = _local_now_strings()
        footer = display.footer_lines(date_str, time_str)
        # WEATHER_ERROR overrides even a previously-good reading -- don't show
        # last-good as current once this pull has failed (see the weather
        # pull above). weather_enabled and no pull yet -> plain None, same as
        # weather disabled, until the first pull attempt resolves either way.
        weather_for_frame = display.WEATHER_ERROR if weather_error else last_weather
        frame = (sections, footer, weather_for_frame)
        flat = []
        for section in sections:
            flat.extend(display.section_lines(section))
        weather_key = "weather: error" if weather_error else weather.summary_text(last_weather)
        rendered_key = "\n".join(flat + footer + [weather_key])

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
            full = full_due or prev_frame is None
            print("display_loop: content changed, %s refresh" % ("full" if full else "partial"))
            _draw_and_refresh(epd, fb, fb_buf, frame, prev_frame, full=full)
            last_rendered = rendered_key
            prev_frame = frame
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

        # A short settle after Wi-Fi connect. It originally fixed intermittent
        # first-TLS-handshake failures; with fetches now plain HTTP that's
        # likely moot, but letting the Wi-Fi stack settle a moment is cheap
        # and harmless, so it stays. (Drop it only with on-device testing.)
        await asyncio.sleep_ms(3000)

        # Warm the font advance caches on this still-clean heap, before the
        # fetch/render loop -- so no font state is allocated during a live
        # draw, where it would strand into the framebuffer region and
        # starve the next TLS handshake (see bitfont.py docstring).
        try:
            display.warm_fonts()
        except Exception as e:
            print("main: font warm failed (non-fatal):", e)

        await display_loop(settings.load(), wifi_cfg)
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
