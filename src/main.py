"""One synchronous deep-sleep wake cycle for the departure display."""
import framebuf
import gc
import machine
import network
import ntptime
import sys
import time

import config
import cycle
import departures
import display
import localtime
import openmeteo
import refresh_txn
import retained
import settings
import sl
import wake_schedule
import weather
import wifi
from epd7in5v2 import EPD7in5V2

if False:
    from typing import Any
    from models import RetainedState, Settings

WDT_TIMEOUT_MS = 150000
_FB_WIDTH = 800
_FB_HEIGHT = 480


def _allocate_framebuffer() -> "tuple[Any, bytearray]":
    """Reserve the one full-screen buffer before radio or RTC work."""
    gc.collect()
    fb_buf = bytearray(_FB_WIDTH * _FB_HEIGHT // 8)
    fb = framebuf.FrameBuffer(fb_buf, _FB_WIDTH, _FB_HEIGHT, framebuf.MONO_HLSB)
    return fb, fb_buf


def _fetch_all_stops(
    cfg: "Settings", wdt: "Any | None" = None,
) -> "list[list[dict[str, str]] | None]":
    results = []  # type: list[list[dict[str, str]] | None]
    for stop in cfg["stops"]:
        if wdt is not None:
            wdt.feed()
        try:
            raw = sl.fetch_departures(
                stop["site_id"],
                forecast=cfg["forecast_min"],
                direction=cfg["direction_code"],
            )
            results.append(departures.parse_departures(raw)[:cfg["departures_per_stop"]])
        except Exception as exc:
            print("fetch: stop %s failed: %s" % (stop["name"], exc))
            results.append(None)
        if wdt is not None:
            wdt.feed()
    return results


def _local_now_strings() -> "tuple[str, str]":
    y, month, day, hour, minute, second, _weekday, _yday = time.gmtime()[:8]
    ly, lmonth, lday, lhour, lminute, _second, _cest = localtime.utc_to_stockholm(
        y, month, day, hour, minute, second,
    )
    return localtime.format_date(ly, lmonth, lday), localtime.format_time(lhour, lminute)


def _local_today_iso() -> str:
    y, month, day, hour, minute, second, _weekday, _yday = time.gmtime()[:8]
    ly, lmonth, lday, _hour, _minute, _second, _cest = localtime.utc_to_stockholm(
        y, month, day, hour, minute, second,
    )
    return "%04d-%02d-%02d" % (ly, lmonth, lday)


def _safe_sleep(epd: EPD7in5V2) -> None:
    try:
        epd.sleep()
    except Exception as exc:
        print("display: panel sleep after a refresh error also failed:", exc)


def _draw_and_refresh(
    epd: EPD7in5V2,
    fb: "Any",
    fb_buf: bytearray,
    frame: "Any",
    prev_frame: "Any | None",
    full: bool,
) -> None:
    """Write a full frame or a true old/new differential, then sleep panel."""
    summary = display.frame_summary(frame)

    if full:
        display.draw_home(fb, frame)
        try:
            epd.init()
            epd.display(fb_buf)
        finally:
            _safe_sleep(epd)
        _print_summary(summary)
        return

    assert prev_frame is not None
    try:
        epd.init_part()
        epd.partial_begin()
        display.draw_home(fb, prev_frame)
        epd.partial_old(fb_buf)
        display.draw_home(fb, frame)
        epd.partial_new(fb_buf)
    finally:
        _safe_sleep(epd)
    _print_summary(summary)


def _print_summary(summary: str) -> None:
    """Best-effort serial corroboration after a successful panel write."""
    try:
        print(summary)
    except Exception:
        pass


def _wait_until_epoch(wdt: "Any", target_epoch: int) -> None:
    while True:
        remaining = target_epoch - time.time()
        if remaining <= 0:
            return
        wdt.feed()
        time.sleep(remaining if remaining < 1 else 1)


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
    state = retained.decode(raw, expected_fingerprint, expected_stop_keys)
    if state is None:
        reason = "empty" if not raw else "invalid/corrupt/incompatible"
        print("retained: %s (%d bytes) -- next refresh must be full" % (reason, len(raw)))
    else:
        print("retained: restored %d bytes -- differential old frame available" % len(raw))
    return state


def _rtc_invalidate() -> None:
    rtc = machine.RTC()
    rtc.memory(b"")
    if rtc.memory() != b"":
        raise OSError("RTC retained-state invalidation failed")


def _rtc_commit(
    raw: bytes,
    expected_fingerprint: str,
    expected_stop_keys: "list[str]",
) -> None:
    try:
        rtc = machine.RTC()
        rtc.memory(raw)
        readback = rtc.memory()
        if readback != raw or retained.decode(
            readback, expected_fingerprint, expected_stop_keys,
        ) is None:
            raise OSError("RTC retained-state readback mismatch")
    except Exception:
        try:
            _rtc_invalidate()
        except Exception as cleanup_error:
            print("retained: cleanup after failed commit also failed:", cleanup_error)
        raise
    print("retained: saved and verified %d/%d bytes" % (len(raw), retained.MAX_BYTES))


def _rtc_state_load(cfg: "Settings") -> "RetainedState | None":
    return _rtc_load(retained.settings_fingerprint(cfg), _retained_stop_keys(cfg))


def _rtc_state_save(state: "RetainedState", cfg: "Settings") -> None:
    _rtc_commit(retained.encode(state), retained.settings_fingerprint(cfg), _retained_stop_keys(cfg))


def deep_sleep_cycle(
    cfg: "Settings",
    connected: bool,
    state: "RetainedState | None",
    request_epoch: int,
    boot_ticks: int,
    last_ntp_epoch: "int | None",
    fb: "Any",
    fb_buf: bytearray,
) -> int:
    """Fetch each SL stop once, weather at most once when due, then persist."""
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
    previous_frame = state["frame"] if state else None
    last_weather_time = state["weather_time"] if state else None

    lead_observed_ms = time.ticks_diff(time.ticks_ms(), boot_ticks)
    print("power: preparation took %d ms; requests target epoch %d" % (lead_observed_ms, request_epoch))
    _wait_until_epoch(wdt, request_epoch)
    print("power: request boundary reached (%+d s late); starting network requests" % int(time.time() - request_epoch))
    wdt.feed()

    results = [None] * len(cfg["stops"])  # type: list[list[dict[str, str]] | None]
    if connected:
        results = _fetch_all_stops(cfg, wdt=wdt)

    weather_cfg = cfg["weather"]
    weather_attempted = False
    weather_result = None  # type: Any
    if (
        connected and weather_cfg is not None and cycle.weather_due(
            int(time.time()), last_weather_time, weather_cfg["pull_interval_min"] * 60,
        )
    ):
        weather_attempted = True
        try:
            wdt.feed()
            weather_result = weather.parse_weather(openmeteo.fetch_today(
                weather_cfg["latitude"], weather_cfg["longitude"],
            ))
            wdt.feed()
            if weather_result is None:
                print("weather: unusable payload")
            else:
                print("weather: " + weather.summary_text(weather_result))
        except Exception as exc:
            print("weather: fetch failed:", exc)

    if connected and (last_ntp_epoch is None or time.time() - last_ntp_epoch >= 24 * 3600):
        try:
            ntptime.settime()
            last_ntp_epoch = int(time.time())
            print("power: NTP resync ok")
        except Exception as exc:
            print("power: NTP resync failed:", exc)

    now_epoch = int(time.time())
    date_str, time_str = _local_now_strings()
    decision = cycle.decide(
        cfg, state, results, connected, weather_attempted, weather_result,
        now_epoch, _local_today_iso(), display.footer_lines(date_str, time_str),
        last_ntp_epoch,
    )
    refresh = decision["refresh"]
    expected_fingerprint = retained.settings_fingerprint(cfg)
    expected_stop_keys = _retained_stop_keys(cfg)
    if refresh != "none":
        print("power: content changed, %s refresh" % refresh)

        def refresh_panel() -> None:
            _draw_and_refresh(
                EPD7in5V2(), fb, fb_buf, decision["frame"], previous_frame,
                refresh == "full",
            )

        def commit_state(raw: bytes) -> None:
            _rtc_commit(raw, expected_fingerprint, expected_stop_keys)

        refresh_txn.apply(
            decision["state"], retained.encode, _rtc_invalidate, refresh_panel,
            commit_state,
        )
    else:
        print("power: content unchanged -- no panel refresh")
        _rtc_commit(retained.encode(decision["state"]), expected_fingerprint, expected_stop_keys)

    advance_s = cfg["power"]["wake_advance_s"]
    delay_s = wake_schedule.next_wake_delay_s(time.time(), advance_s, 60)
    print("power: deep sleep %d s; next wake %d s before minute boundary" % (delay_s, advance_s))
    return delay_s


def _disable_wlan() -> None:
    try:
        network.WLAN(network.STA_IF).active(False)
    except Exception:
        pass


def main() -> None:
    boot_ticks = time.ticks_ms()
    cfg = settings.load()
    wifi_cfg = config.load()
    fb, fb_buf = _allocate_framebuffer()
    state = _rtc_state_load(cfg)
    last_ntp_epoch = state["last_ntp"] if state is not None else None

    connected = False
    if wifi_cfg is not None and wifi_cfg["ssid"]:
        connected = wifi.connect_sta(wifi_cfg["ssid"], wifi_cfg["password"])

    if connected and state is None:
        try:
            ntptime.settime()
            last_ntp_epoch = int(time.time())
            print("main: cold-boot NTP sync ok")
        except Exception as exc:
            print("main: cold-boot NTP sync failed:", exc)

    request_epoch = wake_schedule.request_boundary(time.time(), 60)

    print("main: wake advance = %d s" % cfg["power"]["wake_advance_s"])
    delay_s = deep_sleep_cycle(
        cfg, connected, state, request_epoch, boot_ticks, last_ntp_epoch, fb, fb_buf,
    )
    _disable_wlan()
    machine.deepsleep(delay_s * 1000)


try:
    main()
except KeyboardInterrupt:
    raise
except (settings.SettingsError, config.ConfigError) as exc:
    print("main: configuration/startup error:")
    sys.print_exception(exc)  # type: ignore[attr-defined]
    while True:
        time.sleep(1)
except Exception as exc:
    print("main: unexpected runtime error; retained state invalidated")
    sys.print_exception(exc)  # type: ignore[attr-defined]
    try:
        machine.RTC().memory(b"")
    except Exception:
        pass
    machine.deepsleep(60_000)
