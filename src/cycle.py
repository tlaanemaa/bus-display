"""Pure policy for one boundary-aligned deep-sleep wake.

The caller supplies normalized settings, prior validated RTC state, adapter
results, and all clock-derived values.  This module selects semantic display
content, refresh mode, and the next retained state without touching hardware,
networking, RTC memory, NTP, or the clock.
"""
import display
import retained
import weather

if False:
    from typing import Any
    from models import (
        CycleDecision,
        DepartureResult,
        DisplaySection,
        DisplayStatus,
        RefreshMode,
        RetainedState,
        Settings,
        StopConfig,
        WeatherReading,
    )


def stop_key(stop: "StopConfig", direction_code: int) -> str:
    """Return the stable identity used for retained stop fallback."""
    return "%s:%s" % (stop["site_id"], direction_code)


def weather_due(
    now_epoch: int,
    last_weather_time: "int | None",
    interval_s: int,
) -> bool:
    """Whether another weather adapter attempt is due by elapsed time."""
    if last_weather_time is None:
        return True
    age_s = now_epoch - last_weather_time
    return age_s < 0 or age_s >= interval_s


def _stop_keys(settings: "Settings") -> "list[str]":
    return [
        stop_key(stop, settings["direction_code"])
        for stop in settings["stops"]
    ]


def _compatible_previous(
    settings: "Settings", previous: "RetainedState | None",
) -> bool:
    if previous is None:
        return False
    if (
        previous["v"] != retained.RETAINED_VERSION
        or previous["render_rev"] != retained.RENDER_REVISION
        or previous["settings"] != retained.settings_fingerprint(settings)
    ):
        return False
    return (
        [section["stop_key"] for section in previous["frame"]["sections"]]
        == _stop_keys(settings)
    )


def _previous_sections(
    previous: "RetainedState | None",
) -> "dict[str, DisplaySection]":
    by_key = {}  # type: dict[str, DisplaySection]
    if previous is not None:
        for section in previous["frame"]["sections"]:
            by_key[section["stop_key"]] = section
    return by_key


def _sections(
    settings: "Settings",
    previous: "RetainedState | None",
    departure_results: "list[DepartureResult]",
) -> "list[DisplaySection]":
    old_by_key = _previous_sections(previous)
    sections = []  # type: list[DisplaySection]
    for index, stop in enumerate(settings["stops"]):
        key = stop_key(stop, settings["direction_code"])
        result = departure_results[index]
        if result is not None:
            sections.append(
                display.stop_section(key, stop["name"], result, stale=False)
            )
            continue

        old = old_by_key.get(key)
        if old is None:
            sections.append(
                display.stop_section(key, stop["name"], [], stale=True)
            )
            continue
        section = dict(old)  # type: Any
        section["name"] = stop["name"]
        section["stale"] = True
        sections.append(section)
    return sections


def _weather_state(
    settings: "Settings",
    previous: "RetainedState | None",
    weather_attempted: bool,
    weather_result: "WeatherReading | None",
    now_epoch: int,
    today_iso: str,
) -> "tuple[WeatherReading | None, int | None]":
    weather_cfg = settings["weather"]
    if weather_cfg is None:
        return None, None

    if weather_attempted and weather_result is not None:
        return weather_result, now_epoch

    previous_reading = (
        previous["weather"] if previous is not None else None
    )  # type: Any
    previous_time = previous["weather_time"] if previous is not None else None
    age_s = None if previous_time is None else now_epoch - previous_time
    if weather.keep_last_good(
        previous_reading,
        today_iso,
        age_s,
        weather_cfg["max_age_min"] * 60,
    ):
        return previous_reading, previous_time
    return None, None


def _status(
    connected: bool,
    weather_enabled: bool,
    reading: "WeatherReading | None",
) -> "DisplayStatus":
    if not connected:
        return display.make_status("wifi_error")
    if weather_enabled and reading is None:
        return display.make_status("weather_error")
    if reading is not None:
        return display.make_status("weather", reading)
    return display.make_status("none")


def decide(
    settings: "Settings",
    previous: "RetainedState | None",
    departure_results: "list[DepartureResult]",
    connected: bool,
    weather_attempted: bool,
    weather_result: "WeatherReading | None",
    now_epoch: int,
    today_iso: str,
    footer: "list[str]",
    last_ntp_epoch: "int | None",
) -> "CycleDecision":
    """Propose the exact visible frame, panel action, and retained state."""
    reading, weather_time = _weather_state(
        settings,
        previous,
        weather_attempted,
        weather_result,
        now_epoch,
        today_iso,
    )
    frame = display.make_frame(
        _sections(settings, previous, departure_results),
        footer,
        _status(connected, settings["weather"] is not None, reading),
    )

    changed = previous is None or frame != previous["frame"]
    previous_last_full = previous["last_full"] if previous is not None else None
    refresh = "none"  # type: RefreshMode
    if changed:
        full_due = (
            previous_last_full is None
            or now_epoch - previous_last_full
            >= settings["full_refresh_interval_min"] * 60
        )
        if not _compatible_previous(settings, previous) or full_due:
            refresh = "full"
        else:
            refresh = "partial"

    last_full = now_epoch if refresh == "full" else previous_last_full
    state = {
        "v": retained.RETAINED_VERSION,
        "render_rev": retained.RENDER_REVISION,
        "settings": retained.settings_fingerprint(settings),
        "frame": frame,
        "last_full": last_full,
        "weather": reading,
        "weather_time": weather_time,
        "last_ntp": last_ntp_epoch,
    }  # type: RetainedState
    return {"frame": frame, "refresh": refresh, "state": state}
