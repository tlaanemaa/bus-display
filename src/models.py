"""Type-only runtime contracts shared by the display application.

All declarations stay under ``if False`` so they guide host-side mypy
without importing ``typing`` or allocating type metadata on the ESP32.
"""

if False:
    from typing import Any, Callable, Literal, NotRequired, Protocol, TypedDict

    class StopConfig(TypedDict):
        name: str
        site_id: int

    class WeatherConfig(TypedDict):
        enabled: bool
        latitude: float
        longitude: float
        pull_interval_min: int
        max_age_min: int

    class PowerConfig(TypedDict):
        wake_advance_s: int

    class Settings(TypedDict):
        stops: list[StopConfig]
        direction_code: int
        forecast_min: int
        departures_per_stop: int
        full_refresh_interval_min: int
        power: PowerConfig
        weather: WeatherConfig | None

    class WifiConfig(TypedDict):
        ssid: str
        password: str

    class Departure(TypedDict):
        line: str
        destination: str
        display: str

    class WeatherReading(TypedDict):
        date: str
        condition: str
        tmin: int
        tmax: int
        precip: int | None

    class DisplaySection(TypedDict):
        stop_key: str
        name: str
        hero_main: str | None
        hero_unit: str | None
        badge_line: str | None
        dest: str
        rows: list[list[str]]
        stale: bool

    class DisplayStatus(TypedDict):
        kind: Literal["none", "wifi_error", "weather_error", "weather"]
        reading: NotRequired[WeatherReading]

    class DisplayFrame(TypedDict):
        sections: list[DisplaySection]
        footer: list[str]
        status: DisplayStatus

    class RetainedState(TypedDict):
        v: int
        render_rev: int
        settings: str
        frame: DisplayFrame
        last_full: int | None
        weather: WeatherReading | None
        weather_time: int | None
        last_ntp: int | None

    class CycleDecision(TypedDict):
        frame: DisplayFrame
        refresh: Literal["none", "partial", "full"]
        state: RetainedState

    class FrameBufferLike(Protocol):
        def fill(self, color: int) -> None: pass
        def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None: pass
        def pixel(self, x: int, y: int, color: "int | None" = None) -> Any: pass

    class WatchdogLike(Protocol):
        def feed(self) -> None: pass

    class ResponseLike(Protocol):
        status_code: int
        def json(self) -> object: pass
        def close(self) -> None: pass
