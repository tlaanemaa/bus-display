"""Entry point: boot flow, asyncio loop.

Boot flow (see AGENTS.md "Architecture"): load /settings.json and
/config.json, restore validated semantic display state from RTC memory,
try Wi-Fi STA, wait for the wall-minute request boundary, fetch/render,
save retained state, then enter ESP32 deep sleep. Missing Wi-Fi is shown
explicitly on the panel and retried on the next wake; configuration is
over USB, with no setup portal.

/settings.json is NOT committed to git (see .gitignore) so the owner's
home stop doesn't end up in a public repo -- copy src/settings.example.json
to src/settings.json, fill in your stop(s) (see AGENTS.md "Departures
logic & stops" for how to find a site id), then deploy it like any other
file: `mpremote connect COM3 fs cp src/settings.json :settings.json`.

The 48KB framebuffer is allocated ONCE at boot (in display_loop) and kept
resident, reused for every refresh -- see AGENTS.md "RAM-vs-HTTPS conflict
(RESOLVED)". This reverses an earlier design: the framebuffer used to be
allocated transiently, per cycle, ONLY because a resident buffer starved
the SL TLS handshake (mbedtls's RSA-2048 cert verification needs a large
contiguous block). Now that SL and Open-Meteo are fetched over plain HTTP
(no TLS handshake at all), that pressure is gone -- and a resident buffer
is also more robust, since a fresh 48KB alloc had begun to MemoryError on
later cycles as the heap fragmented (MicroPython's GC never compacts).

The legacy Microdot setup server is not imported or used. It remains in
the tree only as known cleanup debt; importing it would build an unused
route table and waste scarce RAM.
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
import retained
import cycle
import refresh_txn
import wake_schedule
from epd7in5v2 import EPD7in5V2

if False:
    from typing import Any
    from models import RetainedState, Settings, WifiConfig

# `server` (Microdot) is deliberately not imported: USB configuration
# replaced the setup portal, and its top-level route construction costs RAM.

WDT_TIMEOUT_MS = 150000     # hardware watchdog: force a reboot if one display_loop iteration ever takes
                             # longer than this -- a general hang backstop. The TLS hang it was originally
                             # for is now moot (fetches are plain HTTP, see AGENTS.md "RAM-vs-HTTPS conflict
                             # (RESOLVED)"), so this should rarely fire, but a stuck socket read or driver
                             # busy-wait could still hang an iteration. 150s gives headroom over the worst
                             # legitimate case with 2 configured stops: each SL/Open-Meteo adapter makes
                             # one 10s-bounded request, so two stops plus weather take at most ~30s. Weather
                             # may retry on each render tick while its error is visible, but still only once
                             # per tick. If settings.json ever lists many more stops, this may need raising.

_WDT_FEED_CHUNK_S = 60   # feed the watchdog at least this often while idling between ticks, so a render
                          # interval longer than the WDT window can't trip a spurious reboot during a normal wait

_FB_WIDTH = 800
_FB_HEIGHT = 480

# After this many consecutive pulls in which EVERY stop's fetch failed, attempt
# an explicit Wi-Fi reconnect (see wifi.reconnect). All stops failing at once is
# a connectivity signal, not an SL-side one. 3 (~3 min at the default 1-min pull)
# gives the ESP32's own auto-reconnect a chance to recover first before we step in.
_WIFI_RECONNECT_AFTER_FAILS = 3


def _fetch_all_stops(
    cfg: "Settings", wdt: "Any | None" = None,
) -> "list[list[dict[str, str]] | None]":
    """Fetch every configured stop independently, in the order given in
    settings.json -- no primary/fallback/suitability logic anymore, just
    an ordered list of stops the owner cares about (see AGENTS.md
    "Departures logic & stops"). Returns a list, one entry per stop: the
    stop's next cfg["departures_per_stop"] departures, or None if this
    stop's fetch failed this cycle (caller falls back to that stop's own
    cached data)."""
    results = []  # type: list[list[dict[str, str]] | None]
    for stop in cfg["stops"]:
        if wdt is not None:
            wdt.feed()
        try:
            raw = sl.fetch_departures(
                stop["site_id"], forecast=cfg["forecast_min"],
                direction=cfg["direction_code"])
            deps = departures.parse_departures(raw)
            results.append(deps[:cfg["departures_per_stop"]])
        except Exception as e:
            print("fetch: stop %s failed: %s" % (stop["name"], e))
            results.append(None)
        if wdt is not None:
            wdt.feed()
    return results


def _seconds_to_next_tick(interval_s: int) -> int:
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


async def _sleep_until_next_tick(wdt: "Any", interval_s: int) -> None:
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


def _local_now_strings() -> "tuple[str, str]":
    """(date_str, time_str) for the device's current local (Stockholm)
    time, computed from the NTP-synced UTC clock -- see localtime.py."""
    y, mo, d, h, mi, s, _weekday, _yday = time.gmtime()[:8]
    ly, lmo, ld, lh, lmi, _ls, _cest = localtime.utc_to_stockholm(y, mo, d, h, mi, s)
    return localtime.format_date(ly, lmo, ld), localtime.format_time(lh, lmi)


def _local_today_iso() -> str:
    """Current local (Stockholm) date as 'YYYY-MM-DD' -- the same format as
    Open-Meteo's forecast date (weather.parse_weather's 'date'), so a kept
    last-good weather reading can be checked for being still today's. Assumes
    the weather coords share the device's timezone (true here: both Stockholm);
    a far-away weather location could disagree by a day near midnight, which
    would just surface the honest 'Weather error' a bit early."""
    y, mo, d, h, mi, s, _weekday, _yday = time.gmtime()[:8]
    ly, lmo, ld, _lh, _lmi, _ls, _cest = localtime.utc_to_stockholm(y, mo, d, h, mi, s)
    return "%04d-%02d-%02d" % (ly, lmo, ld)


def _safe_sleep(epd: EPD7in5V2) -> None:
    """Best-effort panel power-down, called from a finally so a mid-refresh
    error never leaves the panel powered/active between cycles (e-paper rule
    1 -- leaving it active degrades it). Swallows its own error so it can't
    mask the original refresh exception; a hang inside sleep() itself is the
    hardware watchdog's job, not this function's."""
    try:
        epd.sleep()
    except Exception as e:
        print("display: panel sleep after a refresh error also failed:", e)


def _draw_and_refresh(
    epd: EPD7in5V2,
    fb: "Any",
    fb_buf: bytearray,
    frame: "Any",
    prev_frame: "Any | None",
    full: bool,
) -> None:
    """Draws `frame` into the RESIDENT framebuffer (fb/fb_buf, allocated once
    at boot and passed in -- see display_loop) and pushes it to the panel.

    The framebuffer used to be allocated transiently, per cycle, ONLY because
    a resident 48KB buffer starved the SL TLS handshake (AGENTS.md "RAM-vs-
    HTTPS conflict"). Now that SL and Open-Meteo are fetched over plain HTTP,
    nothing does a TLS handshake, so no big contiguous block is contended --
    a single resident buffer is both safe and BETTER: a fresh 48KB alloc had
    started failing (MemoryError) on later cycles once fetches became reliable,
    because the heap fragments and MicroPython's GC never compacts. Allocating
    once, when the heap is cleanest, sidesteps that entirely.

    Deep-sleep frames are explicit DisplayFrame dictionaries. The legacy awake
    path temporarily still supplies its positional outer frame; the compatibility
    branch below is removed when that path is migrated. `prev_frame` is the
    previously-drawn frame needed as the differential partial's 0x10 old plane.

    `full` picks the refresh mode (see AGENTS.md "Screen refresh strategy"):
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
    frame_data = None  # type: Any
    if full:
        # TEMPORARY Task 4/7 bridge: the awake path still has a positional
        # outer frame while deployed deep sleep already uses DisplayFrame.
        if isinstance(frame, dict):
            display.draw_home(fb, frame)
            frame_data = frame
        else:
            display.draw_home(fb, *frame)
            frame_data = display.make_frame(*frame)
        try:
            epd.init()
            epd.display(fb_buf)
        finally:
            _safe_sleep(epd)
        print(display.frame_summary(frame_data))
        return

    # Differential partial: old plane (0x10) first, then new plane (0x13).
    # init_part() powers the panel, so the whole sequence (including the
    # draw_home re-renders between planes) is inside the try -> always slept.
    assert prev_frame is not None
    try:
        epd.init_part()
        epd.partial_begin()
        if isinstance(prev_frame, dict):
            display.draw_home(fb, prev_frame)
        else:
            display.draw_home(fb, *prev_frame)
        epd.partial_old(fb_buf)
        if isinstance(frame, dict):
            display.draw_home(fb, frame)
            frame_data = frame
        else:
            display.draw_home(fb, *frame)
            frame_data = display.make_frame(*frame)
        epd.partial_new(fb_buf)
    finally:
        _safe_sleep(epd)
    print(display.frame_summary(frame_data))


async def _wait_until_epoch(wdt: "Any", target_epoch: int) -> None:
    """Keep the ESP32 awake but idle until the request boundary."""
    while True:
        remaining = int(target_epoch - time.time())
        if remaining <= 0:
            return
        wdt.feed()
        await asyncio.sleep(remaining if remaining < _WDT_FEED_CHUNK_S else _WDT_FEED_CHUNK_S)


def _retained_stop_keys(cfg: "Settings") -> "list[str]":
    return [
        "%s:%s" % (stop["site_id"], cfg["direction_code"])
        for stop in cfg["stops"]
    ]


def _rtc_load(
    expected_fingerprint: str,
    expected_stop_keys: "list[str]",
) -> "RetainedState | None":
    raw = machine.RTC().memory()
    state = retained.decode(
        raw,
        expected_fingerprint,
        expected_stop_keys,
    )
    if state is None:
        reason = "empty" if not raw else "invalid/corrupt/incompatible"
        print("retained: %s (%d bytes) -- next refresh must be full" % (reason, len(raw)))
    else:
        print("retained: restored %d bytes -- differential old frame available" % len(raw))
    return state


def _rtc_invalidate() -> None:
    """Clear retained bytes and prove the old frame is no longer trusted."""
    rtc = machine.RTC()
    rtc.memory(b"")
    if rtc.memory() != b"":
        raise OSError("RTC retained-state invalidation failed")


def _rtc_commit(
    raw: bytes,
    expected_fingerprint: str,
    expected_stop_keys: "list[str]",
) -> None:
    """Write exact encoded state and independently validate its readback."""
    rtc = machine.RTC()
    rtc.memory(raw)
    readback = rtc.memory()
    # Compare the exact stored bytes, then independently validate decoding.
    # Do not compare decoded JSON to the live Python object: display rows are
    # tuples while JSON canonically restores arrays as lists, so semantically
    # identical state would falsely fail an object-equality check.
    if readback != raw or retained.decode(
        readback,
        expected_fingerprint,
        expected_stop_keys,
    ) is None:
        # A failed verification must never leave bytes that a later wake could
        # mistake for the frame physically on the panel.
        _rtc_invalidate()
        raise OSError("RTC retained-state readback mismatch")
    print("retained: saved and verified %d/%d bytes" % (len(raw), retained.MAX_BYTES))


def _rtc_state_load(cfg: "Settings") -> "RetainedState | None":
    return _rtc_load(
        retained.settings_fingerprint(cfg),
        _retained_stop_keys(cfg),
    )


def _rtc_state_save(state: "RetainedState", cfg: "Settings") -> None:
    """Legacy wrapper for callers that do not own a refresh transaction."""
    _rtc_commit(
        retained.encode(state),
        retained.settings_fingerprint(cfg),
        _retained_stop_keys(cfg),
    )


async def deep_sleep_cycle(
    cfg: "Settings",
    wifi_cfg: "WifiConfig | None",
    connected: bool,
    state: "RetainedState | None",
    request_epoch: int,
    boot_ticks: int,
) -> None:
    """One wake -> boundary-aligned fetch/render -> retained state -> sleep."""
    epd = EPD7in5V2()
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
    gc.collect()
    fb_buf = bytearray(_FB_WIDTH * _FB_HEIGHT // 8)
    fb = framebuf.FrameBuffer(fb_buf, _FB_WIDTH, _FB_HEIGHT, framebuf.MONO_HLSB)

    previous_frame = state["frame"] if state else None
    last_weather_time = state["weather_time"] if state else None
    last_ntp_epoch = state["last_ntp"] if state else None

    lead_observed_ms = time.ticks_diff(time.ticks_ms(), boot_ticks)
    print("power: preparation took %d ms; requests target epoch %d" % (lead_observed_ms, request_epoch))
    await _wait_until_epoch(wdt, request_epoch)
    request_late_s = int(time.time() - request_epoch)
    print("power: request boundary reached (%+d s late); starting network requests" % request_late_s)
    wdt.feed()

    results = [None] * len(cfg["stops"])  # type: list[list[dict[str, str]] | None]
    if connected:
        # A deep-sleep wake is itself the next retry one minute later. Every
        # adapter call makes one bounded request, leaving time for every source
        # even if another source (notably Open-Meteo) has a slow timeout.
        results = _fetch_all_stops(cfg, wdt=wdt)

    weather_cfg = cfg["weather"]
    weather_attempted = False
    weather_result = None  # type: Any
    if (
        connected
        and weather_cfg is not None
        and cycle.weather_due(
            int(time.time()),
            last_weather_time,
            weather_cfg["pull_interval_min"] * 60,
        )
    ):
        weather_attempted = True
        try:
            wdt.feed()
            weather_result = weather.parse_weather(openmeteo.fetch_today(
                weather_cfg["latitude"], weather_cfg["longitude"]))
            wdt.feed()
            if weather_result is None:
                print("weather: unusable payload")
            else:
                print("weather: " + weather.summary_text(weather_result))
        except Exception as e:
            print("weather: fetch failed:", e)

    # RTC survives deep sleep. Resync at most daily, after the boundary so
    # ordinary wakes perform no network request before :00.
    if connected and (last_ntp_epoch is None or time.time() - last_ntp_epoch >= 24 * 3600):
        try:
            ntptime.settime()
            last_ntp_epoch = int(time.time())
            print("power: NTP resync ok")
        except Exception as e:
            print("power: NTP resync failed:", e)

    now_epoch = int(time.time())
    today_iso = _local_today_iso()
    date_str, time_str = _local_now_strings()
    footer = display.footer_lines(date_str, time_str)
    decision = cycle.decide(
        cfg,
        state,
        results,
        connected,
        weather_attempted,
        weather_result,
        now_epoch,
        today_iso,
        footer,
        last_ntp_epoch,
    )
    refresh = decision["refresh"]
    expected_fingerprint = retained.settings_fingerprint(cfg)
    expected_stop_keys = _retained_stop_keys(cfg)
    if refresh != "none":
        print("power: content changed, %s refresh" % refresh)

        def refresh_panel() -> None:
            _draw_and_refresh(
                epd,
                fb,
                fb_buf,
                decision["frame"],
                previous_frame,
                refresh == "full",
            )

        def commit_state(raw: bytes) -> None:
            _rtc_commit(raw, expected_fingerprint, expected_stop_keys)

        refresh_txn.apply(
            decision["state"],
            retained.encode,
            _rtc_invalidate,
            refresh_panel,
            commit_state,
        )
    else:
        print("power: content unchanged -- no panel refresh")
        _rtc_commit(
            retained.encode(decision["state"]),
            expected_fingerprint,
            expected_stop_keys,
        )

    advance_s = cfg.get("power", {}).get("wake_advance_s", 3)
    delay_s = wake_schedule.next_wake_delay_s(time.time(), advance_s, 60)
    print("power: deep sleep %d s; next wake %d s before minute boundary" % (delay_s, advance_s))
    try:
        network.WLAN(network.STA_IF).active(False)
    except Exception:
        pass
    machine.deepsleep(delay_s * 1000)


async def display_loop(
    cfg: "Settings", wifi_cfg: "WifiConfig | None" = None,
) -> None:
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
    as the real minute does. If a tick's work runs long (one 10s-bounded
    request per source), that tick simply lands on a later boundary
    and the next tick re-aligns -- it never drifts into N-min-from-last-wake.

    Three intervals, all in cfg IN MINUTES (see settings.example.json;
    converted to seconds below -- there's no reason to touch an e-ink panel
    more than ~1x/min):
      - data_pull_interval_min    how often to fetch fresh departures from SL
      - render_interval_min       tick cadence: re-render + refresh-if-changed
      - full_refresh_interval_min how often a push is a full (flashing)
                                  refresh vs a differential partial

    Refresh MODE (full vs. partial) -- see AGENTS.md "Screen refresh
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
    last_good = [[] for _ in cfg["stops"]]  # type: list[list[dict[str, str]]]
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

    # Optional today-weather footer (see AGENTS.md "Screen design"). Absent
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
        # fetch will refresh it. openmeteo.fetch_today makes one bounded
        # request (see WDT_TIMEOUT_MS for the worst-case-time math).
        if weather_enabled and isinstance(weather_cfg, dict):
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
                              % (weather.summary_text(last_weather), int(age_s or 0) // 60))
                    else:
                        weather_error = True
                        print("weather: no fresh valid reading for today -- showing error")
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

        sections = [display.stop_section("%s:%s" % (stop["site_id"], cfg["direction_code"]),
                                         stop["name"], deps, stale=sf)
                    for stop, deps, sf in zip(cfg["stops"], last_good, stale_flags)]
        date_str, time_str = _local_now_strings()
        footer = display.footer_lines(date_str, time_str)
        # WEATHER_ERROR overrides even a previously-good reading -- don't show
        # last-good as current once this pull has failed (see the weather
        # pull above). weather_enabled and no pull yet -> plain None, same as
        # weather disabled, until the first pull attempt resolves either way.
        weather_for_frame = (display.make_status("weather_error") if weather_error
                             else (display.make_status("weather", last_weather)
                                   if last_weather else display.make_status("none")))
        frame = (sections, footer, weather_for_frame)
        rendered_key = display.frame_summary(
            display.make_frame(sections, footer, weather_for_frame))

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


async def main() -> None:
    boot_ticks = time.ticks_ms()
    cfg = settings.load()
    power_cfg = cfg.get("power", {})
    deep_sleep = power_cfg.get("deep_sleep", False)
    wake_advance_s = power_cfg.get("wake_advance_s", 3)
    state = _rtc_state_load(cfg) if deep_sleep else None
    request_epoch = wake_schedule.request_boundary(time.time(), 60)
    wifi_cfg = config.load().get("wifi")

    connected = False
    if wifi_cfg and wifi_cfg.get("ssid"):
        connected = wifi.connect_sta(wifi_cfg["ssid"], wifi_cfg.get("password", ""))

    if connected and not deep_sleep:
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

        await display_loop(cfg, wifi_cfg)
    elif deep_sleep:
        # A cold boot has no trustworthy retained wall clock. Sync once before
        # choosing its first request boundary; normal deep-sleep wakes retain
        # the RTC and do their daily NTP request only after the :00 boundary.
        if connected and state is None:
            try:
                ntptime.settime()
                print("main: cold-boot NTP sync ok")
            except Exception as e:
                print("main: cold-boot NTP sync failed:", e)
            request_epoch = wake_schedule.request_boundary(time.time(), 60)
        print("main: deep-sleep mode; wake advance = %d s" % wake_advance_s)
        await deep_sleep_cycle(cfg, wifi_cfg, connected, state, request_epoch, boot_ticks)
    else:
        # No captive portal: USB is the configuration path. Keep retrying on
        # subsequent resets/deep-sleep wakes and never silently look current.
        print("main: Wi-Fi unavailable; AP setup portal is disabled")
        await display_loop(cfg, wifi_cfg)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    raise
except Exception as e:
    print("main: fatal error, idling for recovery (Ctrl-C for REPL):")
    sys.print_exception(e)  # type: ignore[attr-defined]
    while True:
        time.sleep(1)
