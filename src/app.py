"""The application: boot flow, asyncio loop. Started by main.py (a stub).

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
import fieldlog
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

# Robustness layer (added 2026-11-08) -- see CLAUDE.md "Resilience".
_CACHE_PER_STOP = 8          # departures kept per stop (more than shown) so local countdowns can
                             # roll forward through an outage as the soonest buses depart
_SELF_HEAL_AFTER_S = 20 * 60 # no stop has fetched OK for this long -> machine.reset(). The software
                             # equivalent of the owner's unplug/replug: clears leaked sockets, a wedged
                             # lwIP/Wi-Fi stack or a fragmented heap. The cache survives in RTC memory.
_SELF_HEAL_MAX_DOUBLINGS = 3 # back-off: each reset that didn't help doubles the wait (20/40/80/160 min),
                             # so a long SL outage doesn't reboot-loop. Reset to 0 on any good fetch.
# Boot Wi-Fi attempts: wifi.BOOT_ATTEMPTS (connect happens in main.py, before the app loads)
_AP_RETRY_AFTER_S = 10 * 60  # with saved creds, the setup AP reboots after this to retry Wi-Fi
_MIN_VALID_YEAR = 2025       # RTC year below this = clock never set -> can't do local countdowns

# Two schedules per minute (added 2026-11-08, see CLAUDE.md "Timing"):
_NET_OFFSET_S = 20           # network work (SL, weather, NTP) runs at HH:MM:20: off the top-of-minute
                             # rush (SL 429s clustered there), and ~30 s before the next refresh starts
                             # (~10 s lead), so a fetch with a retry or two still finishes in time.
                             # Local countdowns make the data's age irrelevant.
_NET_INTERVAL_S = 60         # how often the network slot comes round (each source has its own bucket)
_LEAD_DEFAULT_MS = {False: 10000, True: 9000}  # first guess at refresh lead: partial, full. Measured on
                             # hardware 2026-11-08: partial = ~9.8 s prep (two full draws + two 48 KB
                             # inverted SPI writes) + ~0.45 s waveform; full = ~5.3 s prep + ~3.7 s flash
_LEAD_MAX_MS = 25000         # never start a refresh more than this before its minute


def _fetch_all_stops(cfg, retries=3):
    """Fetch every configured stop independently, in the order given in
    settings.json -- no primary/fallback/suitability logic anymore, just
    an ordered list of stops the owner cares about (see CLAUDE.md
    "Departures logic & stops"). Returns a list, one entry per stop: the
    stop's next cfg["departures_per_stop"] departures, or None if this
    stop's fetch failed this cycle (caller falls back to that stop's own
    cached data). Also returns a parallel list of error strings (None on
    success) so state changes can go to the field log with their cause."""
    results = []
    errors = []
    for stop in cfg["stops"]:
        try:
            raw = sl.fetch_departures(stop["site_id"], forecast=cfg["forecast_min"],
                                      direction=cfg["direction_code"], retries=retries)
            deps = departures.parse_departures(raw)
            # Cache MORE than we show: local countdowns drop departed buses, and
            # the spares roll up into view during an outage.
            results.append(deps[:max(_CACHE_PER_STOP, cfg["departures_per_stop"])])
            errors.append(None)
        except Exception as e:
            print("fetch: stop %s failed: %s" % (stop["name"], e))
            results.append(None)
            errors.append("%s: %s" % (type(e).__name__, e))
    return results, errors


async def _sleep_until_ms(wdt, target_ms):
    """Await wall-clock time target_ms (see _now_ms), feeding the watchdog
    at least every _WDT_FEED_CHUNK_S so a long idle wait can't trip it. The
    clock is re-read each chunk, so an NTP step mid-wait can't oversleep a
    tick by more than one chunk. The ESP32 stays awake during asyncio.sleep,
    so chunking costs nothing."""
    while True:
        wdt.feed()
        remaining = target_ms - _now_ms()
        if remaining <= 0:
            return
        chunk = remaining if remaining < _WDT_FEED_CHUNK_S * 1000 else _WDT_FEED_CHUNK_S * 1000
        await asyncio.sleep(chunk / 1000)


def _now_ms():
    """Wall-clock milliseconds (time.time() is whole seconds on the ESP32)."""
    return time.time_ns() // 1000000


def _local_now_strings(at_s=None):
    """(date_str, time_str) for local (Stockholm) time at at_s (default:
    now), computed from the NTP-synced UTC clock -- see localtime.py."""
    y, mo, d, h, mi, s, _weekday, _yday = (time.gmtime() if at_s is None else time.gmtime(at_s))[:8]
    ly, lmo, ld, lh, lmi, _ls, _cest = localtime.utc_to_stockholm(y, mo, d, h, mi, s)
    return localtime.format_date(ly, lmo, ld), localtime.format_time(lh, lmi)


def _clock_valid():
    """True once the RTC holds a real date (NTP succeeded at some point --
    the RTC also keeps time across machine.reset()). Local countdowns need
    it; without it we fall back to SL's own `display` strings."""
    return time.gmtime()[0] >= _MIN_VALID_YEAR


def _local_now_tuple(at_s=None):
    """Stockholm local time at at_s (default: now) as (y, mo, d, h, mi, s)."""
    y, mo, d, h, mi, s = (time.gmtime() if at_s is None else time.gmtime(at_s))[:6]
    return localtime.utc_to_stockholm(y, mo, d, h, mi, s)[:6]


# RTC memory layout: a 16-byte BREADCRUMB (the phase the loop is in -- "fetch 0",
# "refresh", "weather", ...) followed by the JSON state blob. The breadcrumb is
# what makes a watchdog reset diagnosable: the WDT gives no traceback, but on the
# next boot the crumb says what was running when it hung, and that goes into the
# field log. Rewriting it is a 2 KB memcpy into RTC RAM (no flash), so phases can
# be marked freely.
_CRUMB_LEN = 16
_rtc_state = {"crumb": b"boot", "blob": b""}


def _rtc_write():
    try:
        c = _rtc_state["crumb"][:_CRUMB_LEN]
        machine.RTC().memory(c + b" " * (_CRUMB_LEN - len(c)) + _rtc_state["blob"])
    except Exception as e:
        print("state: RTC memory write failed:", e)


def _crumb(phase):
    """Mark the loop phase (see _CRUMB_LEN comment)."""
    _rtc_state["crumb"] = phase.encode()
    _rtc_write()


def _read_rtc():
    """(breadcrumb_str, state_blob) left by the previous boot ('' / b'' after
    power loss, when RTC memory is cleared)."""
    try:
        mem = bytes(machine.RTC().memory())
    except Exception:
        return "", b""
    if len(mem) < _CRUMB_LEN:
        return "", b""
    try:
        crumb = mem[:_CRUMB_LEN].decode().strip()
    except Exception:
        crumb = "?"
    return crumb, mem[_CRUMB_LEN:]


def _reset_cause_name():
    try:
        c = machine.reset_cause()
        for name in ("PWRON_RESET", "HARD_RESET", "WDT_RESET", "DEEPSLEEP_RESET", "SOFT_RESET"):
            if getattr(machine, name, None) == c:
                return name
        return str(c)
    except Exception:
        return "?"


def _log_stamp():
    """Field-log timestamp: local wall time once the clock is valid, else
    seconds since boot (ticks) so pre-NTP lines are still ordered."""
    if _clock_valid():
        y, mo, d, h, mi, s = _local_now_tuple()
        return "%04d-%02d-%02d %02d:%02d:%02d" % (y, mo, d, h, mi, s)
    return "up+%ds" % (time.ticks_ms() // 1000)


def _save_cache(cfg, caches, fetch_times, extra):
    """Stash the departures cache + `extra` (on-panel frame, last full-refresh
    time, self-heal backoff count) in RTC memory (survives machine.reset() and
    watchdog resets, not power loss; RAM, so no flash wear) so a reboot comes
    back showing the same buses, WITHOUT a full flashing refresh."""
    try:
        ids = [s["site_id"] for s in cfg["stops"]]
        blob = departures.encode_cache(ids, caches, fetch_times, extra)
        if blob is None and extra and extra.get("frame") is not None:
            # Too big even with the cache trimmed: drop the frame (-> next boot
            # does one full refresh) rather than leave an OLD blob whose frame
            # no longer matches the panel.
            print("state: frame too big for RTC memory, saving without it")
            blob = departures.encode_cache(ids, caches, fetch_times, dict(extra, frame=None))
        _rtc_state["blob"] = blob or b""
        _rtc_write()
    except Exception as e:
        print("cache: save to RTC memory failed:", e)


def _load_cache(cfg, blob):
    try:
        return departures.decode_cache(blob, [s["site_id"] for s in cfg["stops"]])
    except Exception as e:
        print("cache: load from RTC memory failed:", e)
        return None


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
    # Returns (prep_ms, wave_ms): start -> waveform trigger, and the waveform
    # itself (None if unknown). display_loop uses them to start the next
    # refresh early enough that the change lands on the minute (CLAUDE.md
    # "Timing").
    #
    # draw_home() for the full path is pure framebuffer work and runs BEFORE
    # the panel is powered, so it needs no sleep guard; from epd.init() onward
    # the panel is powered, so everything past it is wrapped to guarantee a
    # power-down (see _safe_sleep) even if a write/busy-wait throws mid-refresh.
    t0 = time.ticks_ms()
    epd.trigger_ms = None
    if full:
        display.draw_home(fb, *frame)
        try:
            epd.init()
            epd.display(fb_buf)
            t_done = time.ticks_ms()
        finally:
            _safe_sleep(epd)
        return _refresh_times(epd, t0, t_done)

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
        t_done = time.ticks_ms()
    finally:
        _safe_sleep(epd)
    return _refresh_times(epd, t0, t_done)


def _refresh_times(epd, t0, t_done):
    trig = getattr(epd, "trigger_ms", None)
    if trig is None:
        return None
    return time.ticks_diff(trig, t0), time.ticks_diff(t_done, trig)


async def display_loop(cfg, wifi_cfg=None, rtc_blob=b"", fb_buf=None):
    """Long-lived task. Ticks once per render interval, each tick
    re-rendering from cached departures + the current clock and pushing a
    panel refresh only when the rendered text actually changed (e-paper
    rule 2). Fresh SL data is pulled on its own cadence, gated independently
    of the render tick (both default 1 min but separately tunable -- e.g.
    render every minute for a live clock while pulling data less often to be
    gentler on the API).

    Timing is aligned to the WALL CLOCK, in two slots per minute (CLAUDE.md
    "Timing"): network work at HH:MM:20, and each screen refresh started
    early by a measured lead so the panel changes AT HH:MM:00, with the frame
    computed as of that boundary. If a slot's work runs long (worst case an
    outage's timeouts), the render simply happens late for the current
    minute and the next one re-aligns -- nothing drifts.

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
    display.draw_home), the others are untouched. The badge is AGE-driven
    (stale_after_min), and countdowns are recomputed locally each render --
    see CLAUDE.md "Resilience".
    """
    epd = EPD7in5V2()
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
    # Resident framebuffer: allocated ONCE here (the heap is at its cleanest,
    # ~90KB contiguous) and reused for every refresh. Safe now that nothing
    # does TLS (SL + Open-Meteo are plain HTTP) and more robust than the old
    # per-cycle alloc, which had begun to MemoryError as the heap fragmented.
    # See _draw_and_refresh.
    # Normally main.py allocates it BEFORE importing anything (see main.py);
    # this fallback only runs if display_loop is started some other way.
    if fb_buf is None:
        gc.collect()
        fb_buf = bytearray(_FB_WIDTH * _FB_HEIGHT // 8)
    fb = framebuf.FrameBuffer(fb_buf, _FB_WIDTH, _FB_HEIGHT, framebuf.MONO_HLSB)
    last_rendered = None
    last_full_refresh_time = None  # time.time() of the last FULL refresh (wall clock so it persists across resets)
    last_pull_bucket = None  # (wall clock - _NET_OFFSET_S) // data_pull_interval_s of the last fetch
    last_net_slot = None     # network slots come at HH:MM:20 (see _NET_OFFSET_S); None = run one now
    render_target = None     # wall-clock second of the next render (a minute boundary); None = ASAP
    lead_ms = dict(_LEAD_DEFAULT_MS)  # {is_full: ms}: how early to start a refresh so it lands on time
    lead_learned = {False: False, True: False}  # first measurement replaces the default outright
    # Config is in MINUTES (there's no reason to touch an e-ink panel more than
    # ~1x/min); converted to seconds here for the timing math.
    data_pull_interval_s = cfg.get("data_pull_interval_min", 1) * 60
    render_interval_s = cfg.get("render_interval_min", 1) * 60
    full_refresh_interval_s = cfg.get("full_refresh_interval_min", 30) * 60
    n_stops = len(cfg["stops"])
    last_good = [[] for _ in cfg["stops"]]     # each stop's last-known departures (up to _CACHE_PER_STOP)
    last_good_time = [None] * n_stops          # time.time() of each stop's last SUCCESSFUL fetch
    # STALE is age-driven: with local countdowns a single failed pull is harmless
    # (the cached times keep ticking correctly), so the badge only appears once a
    # stop's data is genuinely old. See CLAUDE.md "Resilience".
    stale_after_s = cfg.get("stale_after_min", 5) * 60
    last_any_success_ticks = time.ticks_ms()   # self-heal reset timer (ticks: immune to NTP clock jumps)
    have_fetched = False  # has ANY pull attempt completed yet? (distinct from "the data is empty")
    consecutive_all_failed = 0  # pulls in a row where EVERY stop errored -> Wi-Fi reconnect trigger
    last_pull_all_failed = False  # outage mode: fetch each stop once, not 3x (keeps ticks under a minute)
    prev_frame = None     # last-drawn (sections, footer, weather); the 0x10 old plane for the next partial
    heal_count = 0        # self-heal resets in a row that didn't fix anything (back-off exponent)

    # Restore state that survived a reset (RTC memory -- see _save_cache). The
    # PANEL also survived (e-ink holds its image unpowered), so restoring the
    # frame that is physically on it lets the first post-reboot refresh be a
    # differential partial, and restoring last_full_refresh_time keeps the
    # full-flash cadence across reboots: a reboot costs the panel NOTHING
    # beyond a normal minute. Only power loss (RTC memory cleared) or a reset
    # DURING a refresh (frame saved as None just before -- panel state unknown)
    # forces a full refresh, which is then genuinely needed.
    restored = _load_cache(cfg, rtc_blob)
    if restored is not None:
        last_good, last_good_time, extra = restored
        extra = extra or {}
        prev_frame = display.frame_from_json(extra.get("frame")) if extra.get("frame") else None
        last_full_refresh_time = extra.get("full_t")
        heal_count = extra.get("heal", 0)
        fieldlog.event("state restored: departures=%s frame=%s heal=%d"
                       % ([len(c) for c in last_good], prev_frame is not None, heal_count))
    else:
        fieldlog.event("state: none restored (power-on, first boot, or stops changed)")

    # Field-log bookkeeping (see fieldlog.py): log state CHANGES, not every pull.
    stop_down_since = [None] * n_stops   # ticks_ms when this stop started failing (None = up)
    stop_fail_count = [0] * n_stops
    hb = {"pulls": 0, "pull_fail": 0, "full": 0, "partial": 0, "reconnects": 0}  # counters since last heartbeat
    reconnect_logged = False  # only the FIRST forced reconnect of an outage gets its own line (flash wear)
    last_hb_bucket = int(time.time() // 3600)
    boot_ticks = time.ticks_ms()
    ntp_failing = False

    def save_state(frame_on_panel):
        _save_cache(cfg, last_good, last_good_time,
                    {"frame": frame_on_panel, "full_t": last_full_refresh_time, "heal": heal_count})

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
    # How stale a last-good reading may be and still be shown during a fetch
    # outage before we fall back to "Weather error". Bounds the outage
    # fallback because Open-Meteo revises the daily forecast through the day
    # (see weather.keep_last_good). Default 3h (~one ICON re-run); configurable.
    weather_max_age_s = (weather_cfg.get("max_age_min", 180) if weather_cfg else 180) * 60
    last_weather_bucket = None
    last_weather = None   # last-good weather summary (kept for logging even while erroring)
    last_weather_time = None  # time.time() of the last SUCCESSFUL weather pull (freshness cap)
    weather_error = False  # this pull failed/unusable -> render WEATHER_ERROR, not the stale reading

    def full_due_at(t):
        # Will a refresh at wall-clock second t be a full one? (Also picks
        # which lead to use when scheduling it.)
        since = None if last_full_refresh_time is None else t - last_full_refresh_time
        return prev_frame is None or since is None or since < 0 or since >= full_refresh_interval_s

    while True:
        wdt.feed()

        # Two schedules (see CLAUDE.md "Timing"): all NETWORK work runs in a
        # slot at HH:MM:20, and the SCREEN refresh is started just early
        # enough to land on HH:MM:00. Fetching off the minute dodges the
        # top-of-minute rush that got SL answering HTTP 429; the data being
        # 30 s older costs nothing because countdowns are computed locally
        # from absolute times at render. The first slot runs at once (boot).
        net_slot = int((time.time() - _NET_OFFSET_S) // _NET_INTERVAL_S)
        if net_slot != last_net_slot:
            last_net_slot = net_slot
            # Pull fresh departures on the wall-clock-aligned data_pull cadence,
            # independent of the render tick. The bucket is integer
            # wall-clock-seconds // interval, so a pull fires exactly once per
            # interval regardless of sub-second tick jitter (and aligns pulls to
            # the clock the same way the render tick is aligned).
            pull_bucket = int((time.time() - _NET_OFFSET_S) // data_pull_interval_s)
            if pull_bucket != last_pull_bucket:
                try:
                    # Outage mode: if the last pull failed for EVERY stop, try each
                    # stop once instead of 3x. Full retries during an outage made each
                    # tick take ~2 min (2 stops x 3 x 10 s timeouts), so the footer
                    # clock skipped minutes. Found with the host simulation.
                    _crumb("fetch")
                    results, errors = _fetch_all_stops(cfg, retries=1 if last_pull_all_failed else 3)
                except Exception as e:
                    fieldlog.exception("unexpected fetch error", e)
                    results = [None] * len(cfg["stops"])
                    errors = ["unexpected"] * len(cfg["stops"])
                _crumb("loop")

                # Field log: one line when a stop goes down (with the cause), one when
                # it comes back (with how long it was out and how many pulls failed).
                hb["pulls"] += 1
                for i, err in enumerate(errors):
                    name = cfg["stops"][i]["name"]
                    if err is not None:
                        hb["pull_fail"] += 1
                        stop_fail_count[i] += 1
                        if stop_down_since[i] is None:
                            stop_down_since[i] = time.ticks_ms()
                            fieldlog.event("stop '%s' DOWN: %s (free=%d %s)" % (name, err, gc.mem_free(), _idf_mem()))
                    elif stop_down_since[i] is not None:
                        out_s = time.ticks_diff(time.ticks_ms(), stop_down_since[i]) // 1000
                        fieldlog.event("stop '%s' UP after %dm%02ds, %d failed pulls"
                                       % (name, out_s // 60, out_s % 60, stop_fail_count[i]))
                        stop_down_since[i] = None
                        stop_fail_count[i] = 0

                now_s = time.time()
                for i, r in enumerate(results):
                    if r is not None:
                        last_good[i] = r
                        last_good_time[i] = now_s
                failed = [r is None for r in results]
                last_pull_all_failed = all(failed)
                if not all(failed):
                    last_any_success_ticks = time.ticks_ms()
                    heal_count = 0
                    save_state(prev_frame)
                last_pull_bucket = pull_bucket
                have_fetched = True
                print("pull: %s free=%d" % (" ".join("FAIL" if f else "ok" for f in failed), gc.mem_free()))

                # Every stop failing at once points at connectivity (Wi-Fi/router),
                # not one stop's SL data. After a few such pulls in a row, force a
                # reconnect -- the ESP32 usually self-heals, but a router power-cycle
                # is the likeliest 24/7 outage and auto-reconnect isn't guaranteed.
                # Counter resets on any success or after an attempt, so we never
                # hammer: at most one reconnect per _WIFI_RECONNECT_AFTER_FAILS pulls.
                if all(failed):
                    consecutive_all_failed += 1
                else:
                    consecutive_all_failed = 0
                    reconnect_logged = False
                if (consecutive_all_failed >= _WIFI_RECONNECT_AFTER_FAILS
                        and wifi_cfg and wifi_cfg.get("ssid")):
                    _crumb("reconnect")
                    try:
                        ok = wifi.reconnect(wifi_cfg["ssid"], wifi_cfg.get("password", ""), force=True)
                        hb["reconnects"] += 1
                        if not reconnect_logged:
                            fieldlog.event("wifi: forced reconnect -> %s (repeats counted in hb)" % ("ok" if ok else "FAILED"))
                            reconnect_logged = True
                    except Exception as e:
                        fieldlog.exception("wifi: forced reconnect raised", e)
                    _crumb("loop")
                    consecutive_all_failed = 0

                # Last resort: nothing has worked for _SELF_HEAL_AFTER_S even after
                # forced reconnects -> reboot, exactly what unplugging did by hand.
                # Leaked sockets / a wedged network stack / a fragmented heap only
                # clear on a reset. The cache is already in RTC memory, so the screen
                # comes back with the same (still ticking) departures and the frame
                # on the panel, so the reboot costs no full flash. Each reset that
                # didn't help doubles the wait (back-off), so a long SL-side outage
                # reboots a handful of times, not every 20 min all day.
                heal_after_s = _SELF_HEAL_AFTER_S << min(heal_count, _SELF_HEAL_MAX_DOUBLINGS)
                if time.ticks_diff(time.ticks_ms(), last_any_success_ticks) > heal_after_s * 1000:
                    fieldlog.event("SELF-HEAL RESET #%d: no successful fetch for %d min (free=%d)"
                                   % (heal_count + 1, heal_after_s // 60, gc.mem_free()), force=True)
                    heal_count += 1
                    _rtc_state["crumb"] = b"selfheal"
                    save_state(prev_frame)
                    time.sleep(1)  # let the print drain
                    machine.reset()

            # Re-sync the RTC from NTP on the slow daily bucket (see above). Kept
            # inside the loop, not just at boot, so long-uptime drift and a failed
            # boot sync both self-heal. On failure we DON'T advance the bucket, so
            # it retries every tick until it succeeds (bounded by ntptime's own
            # ~1s socket timeout; NTP outages are rare) -- same retry-until-good
            # shape as the weather_error path below.
            ntp_bucket = int(time.time() // ntp_resync_interval_s)
            if ntp_bucket != last_ntp_bucket:
                _crumb("ntp")
                try:
                    ntptime.settime()
                    last_ntp_bucket = ntp_bucket
                    if ntp_failing:
                        fieldlog.event("ntp: resync ok again")
                    ntp_failing = False
                except Exception as e:
                    if not ntp_failing:
                        fieldlog.event("ntp: resync FAILED: %s" % e)
                    ntp_failing = True
                _crumb("loop")

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
                weather_bucket = int((time.time() - _NET_OFFSET_S) // weather_pull_interval_s)
                if weather_bucket != last_weather_bucket or weather_error:
                    fetched = None
                    was_error = weather_error
                    _crumb("weather")
                    try:
                        raw = openmeteo.fetch_today(weather_cfg["latitude"], weather_cfg["longitude"])
                        fetched = weather.parse_weather(raw)
                        if fetched is None:
                            print("weather: unusable payload")
                    except Exception as e:
                        print("weather: fetch failed:", e)
                    if fetched is not None:
                        last_weather = fetched
                        last_weather_time = time.time()
                        weather_error = False
                        print("weather: " + weather.summary_text(fetched))
                    else:
                        # Keep the last-good reading only if it's still today's
                        # forecast AND fresh enough (within weather_max_age_s) --
                        # the daily forecast gets revised through the day, so an
                        # unbounded "any time today" fallback could go stale during
                        # a long outage. Otherwise show the honest error.
                        age_s = None if last_weather_time is None else int(time.time() - last_weather_time)
                        if weather.keep_last_good(last_weather, _local_today_iso(), age_s, weather_max_age_s):
                            weather_error = False
                            print("weather: fetch failed -- keeping last-good (%s, %d min old)"
                                  % (weather.summary_text(last_weather), age_s // 60))
                        else:
                            weather_error = True
                            print("weather: no fresh valid reading for today -- showing error")
                    last_weather_bucket = weather_bucket
                    _crumb("loop")
                    if weather_error != was_error:
                        fieldlog.event("weather: %s" % ("ERROR shown (no fresh reading)" if weather_error else "ok again"))

        # Skip rendering ONLY before the very first pull attempt completes --
        # tracked by have_fetched, NOT inferred from "last_good is empty".
        # A successful pull that legitimately returns zero departures (a normal
        # nighttime state for a sparse stop) also leaves last_good empty, and
        # that case MUST fall through to render "No departures" -- otherwise the
        # panel would keep silently displaying the last evening's departures
        # with no STALE badge (the exact stale-mistaken-for-current failure the
        # badges exist to prevent). Once have_fetched is set, every render slot
        # renders: real data, "No departures", or STALE, as applicable.
        #
        # The render is due lead_ms BEFORE render_target, and the frame is
        # computed AS OF render_target (clock, countdowns, STALE), so what
        # appears on the panel at HH:MM:00 is what is true at HH:MM:00. If a
        # long network slot made us late, render_target is in the past and we
        # render for "now" instead.
        render_due = have_fetched and (
            render_target is None
            or _now_ms() >= render_target * 1000 - lead_ms[full_due_at(render_target)])
        if render_due:
            at_s = time.time() if render_target is None else max(render_target, time.time())
            render_target = at_s - at_s % render_interval_s + render_interval_s
            # Countdowns are computed HERE, every render, from each departure's
            # absolute `expected` time vs our own clock -- not frozen at whatever
            # SL said at fetch time. So a failed pull no longer freezes "5 min" on
            # screen; the cached buses keep counting down and drop off once gone.
            # Unset clock (NTP never succeeded) -> fall back to SL's own strings.
            # Everything is evaluated at at_s (the minute this frame is FOR),
            # not at the moment we happen to start drawing it.
            now_local = _local_now_tuple(at_s) if _clock_valid() else None
            per_stop = cfg["departures_per_stop"]
            sections = []
            for stop, deps, t in zip(cfg["stops"], last_good, last_good_time):
                shown = deps if now_local is None else departures.with_local_countdowns(deps, now_local)
                stale = t is None or (at_s - t) > stale_after_s
                sections.append(display.stop_section(stop["name"], shown[:per_stop], stale=stale))
            date_str, time_str = _local_now_strings(at_s)
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

            # Only refresh when content actually changed (e-paper rule 2). The
            # mode is full when a full refresh is due (wall clock, not
            # ticks_ms, so the cadence survives a reset via RTC memory; a clock
            # jump at worst makes one full due early) or on the very first
            # refresh (no previous frame to differential against), otherwise
            # the non-flashing differential partial. Since we only partial on a
            # content change, ghosting only accumulates when we're actually
            # redrawing -- so gating the periodic full on content_changed too is
            # correct: nothing to clear if nothing has been redrawing.
            if rendered_key != last_rendered:
                full = full_due_at(at_s)
                # Mark the panel's content UNKNOWN for the duration of the refresh:
                # a reset mid-refresh leaves a half-driven panel, and the next boot
                # must then do a full refresh, not a differential against a guess.
                save_state(None)
                _crumb("refresh full" if full else "refresh part")
                start_ms = _now_ms()
                times = _draw_and_refresh(epd, fb, fb_buf, frame, prev_frame, full=full)
                _crumb("loop")
                hb["full" if full else "partial"] += 1
                last_rendered = rendered_key
                prev_frame = frame
                if full:
                    last_full_refresh_time = at_s
                save_state(prev_frame)
                # Learn the lead: the new image shows at the END of a full
                # refresh's flashing, and about halfway through a partial's
                # gentle waveform. Averaged so one slow draw can't swing it.
                if times is not None:
                    prep, wave = times
                    seen = prep + (wave if full else wave // 2)
                    lead_ms[full] = min(_LEAD_MAX_MS, (lead_ms[full] + seen) // 2 if lead_learned[full] else seen)
                    lead_learned[full] = True
                    minute_ms = (at_s - at_s % 60) * 1000
                    print("display: %s refresh for %s, started %+d ms vs :00, prep %d + wave %d ms, lands %+d ms, lead now %d"
                          % ("full" if full else "partial", time_str, start_ms - minute_ms, prep, wave,
                             start_ms + seen - minute_ms, lead_ms[full]))
                else:
                    print("display: %s refresh for %s" % ("full" if full else "partial", time_str))
        gc.collect()

        # Hourly heartbeat: proves the device was alive and gives the baseline
        # (free heap trend = leak detector; refresh counts = panel wear) that
        # makes the event lines above interpretable. ~24 lines/day.
        hb_bucket = int(time.time() // 3600)
        if hb_bucket != last_hb_bucket:
            up_s = time.ticks_diff(time.ticks_ms(), boot_ticks) // 1000
            stale_now = [t is None or (time.time() - t) > stale_after_s for t in last_good_time]
            fieldlog.event("hb up=%dh%02dm free=%d %s pulls=%d fail=%d reconnects=%d refresh full=%d part=%d stale=%s heal=%d lead=%d/%d"
                           % (up_s // 3600, up_s % 3600 // 60, gc.mem_free(), _idf_mem(), hb["pulls"], hb["pull_fail"],
                              hb["reconnects"], hb["full"], hb["partial"], stale_now, heal_count,
                              lead_ms[False], lead_ms[True]))
            for k in hb:
                hb[k] = 0
            last_hb_bucket = hb_bucket

        # Sleep to whichever comes first: the next network slot, or the next
        # render's start (render_target minus its lead). _sleep_until_ms feeds
        # the WDT throughout, so a long network slot and the idle wait are
        # bounded separately, not summed, against the 150 s WDT window.
        _crumb("sleep")
        wake_ms = ((last_net_slot + 1) * _NET_INTERVAL_S + _NET_OFFSET_S) * 1000
        if have_fetched and render_target is not None:
            r_ms = render_target * 1000 - lead_ms[full_due_at(render_target)]
            if r_ms < wake_ms:
                wake_ms = r_ms
        await _sleep_until_ms(wdt, wake_ms)


def _idf_mem():
    """'idf=<free>/<largest>' of the ESP-IDF heap -- the pool the Wi-Fi/lwIP
    stack allocates from AND the MicroPython heap grows into. The number that
    actually predicts network failure on this board (DNS -202, Wi-Fi "Internal
    State Error"), so it goes in the field log next to gc free."""
    try:
        import esp32
        regions = esp32.idf_heap_info(esp32.HEAP_DATA)
        return "idf=%d/%d" % (sum(r[1] for r in regions), max(r[2] for r in regions))
    except Exception:
        return "idf=?"


async def main(fb_buf=None, boot_wifi=None):
    # Field log first thing: WHY did we boot (power-on / WDT / self-heal...),
    # and what was the previous boot doing (breadcrumb) when it ended.
    fieldlog.stamp = _log_stamp
    crumb, rtc_blob = _read_rtc()
    fieldlog.event("BOOT cause=%s last_phase=%s free=%d %s" % (_reset_cause_name(), crumb or "-", gc.mem_free(), _idf_mem()), force=True)
    _rtc_state["blob"] = rtc_blob
    _crumb("boot")

    wifi_cfg = config.load().get("wifi")

    have_creds = bool(wifi_cfg and wifi_cfg.get("ssid"))
    if boot_wifi is None:   # main.py normally connects BEFORE importing us (RAM order)
        boot_wifi = wifi.boot_connect(wifi_cfg)
    connected, failed_attempts = boot_wifi
    if failed_attempts:
        fieldlog.event("wifi: %d boot attempt(s) failed before %s"
                       % (failed_attempts, "connecting" if connected else "giving up"))

    if connected:
        try:
            ntptime.settime()
            print("main: NTP sync ok")
        except Exception as e:
            fieldlog.event("ntp: boot sync FAILED: %s" % e)

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

        await display_loop(settings.load(), wifi_cfg, rtc_blob, fb_buf)
    else:
        fieldlog.event("wifi: entering setup AP (creds=%s)" % have_creds)
        _crumb("setup ap")
        wifi.start_ap()
        print("main: no/failed Wi-Fi config -- setup form at http://192.168.4.1")
        print("main: starting admin server on port 80")
        import server
        if have_creds:
            # Saved creds that didn't connect usually means the router is still
            # booting / down, not that they're wrong. Don't sit in setup mode
            # forever: reboot after a while and retry Wi-Fi. Submitting the
            # form reboots sooner anyway.
            async def _retry_sta_later():
                await asyncio.sleep(_AP_RETRY_AFTER_S)
                fieldlog.event("wifi: setup AP idle %d min with saved creds -- rebooting to retry"
                               % (_AP_RETRY_AFTER_S // 60))
                machine.reset()
            asyncio.create_task(_retry_sta_later())
        await server.app.start_server(port=80)


# A crash before display_loop creates the watchdog used to idle FOREVER (found
# on hardware 2026-11-08: a boot-time MemoryError froze the kitchen display
# until a replug). Now the crash handler waits, then resets. Still Ctrl-C-
# recoverable (CLAUDE.md: never a tight reset loop): the 1 s sleeps are
# interruptible, and 5 min between attempts keeps a deterministic boot crash
# to ~2 forced log lines per 5 min.
_FATAL_RESET_AFTER_S = 300


def start(fb_buf=None, boot_wifi=None):
    """Entry point, called by the tiny main.py stub (see main.py for why the
    app lives in this precompiled module instead)."""
    try:
        asyncio.run(main(fb_buf, boot_wifi))
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print("main: fatal error -- resetting in %d s (Ctrl-C for REPL):" % _FATAL_RESET_AFTER_S)
        sys.print_exception(e)
        fieldlog.exception("FATAL (reset in %d s)" % _FATAL_RESET_AFTER_S, e, force=True)
        for _ in range(_FATAL_RESET_AFTER_S):
            time.sleep(1)
        machine.reset()
