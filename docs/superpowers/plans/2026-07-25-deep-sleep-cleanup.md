# Deep-Sleep-Only Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dual-mode firmware with one typed, testable, synchronous deep-sleep wake cycle whose retained old-frame state fails safely.

**Architecture:** Validate JSON configuration once, keep wake-cycle decisions in a pure `cycle.py` module, and leave `main.py` as a thin hardware orchestrator. Encode the next retained state before a panel change, invalidate RTC memory before the refresh, and commit the new state only after the panel refresh succeeds.

**Tech Stack:** MicroPython v1.28.0 on ESP32, CPython 3.12 host tests, pytest, mypy, mpy-cross, mpremote, Waveshare 7.5" V2 e-paper.

## Global Constraints

- Deep sleep is the only supported runtime; do not preserve a continuous-operation switch.
- The cadence is exactly one minute, aligned to the wall-clock minute.
- Keep `power.wake_advance_s`, defaulting to `3`; remove `power.deep_sleep`.
- Make one HTTP attempt per stop and one weather attempt when due; the next wake is the retry.
- Missing or unavailable Wi-Fi must be visible on the screen and retried on later wakes.
- Never allocate two 48 KB framebuffers.
- Preserve the tested EPD command bytes, pin mapping, BUSY polling, polarity, differential old/new plane order, and `finally`-guarded panel sleep.
- A partial refresh may run only with a validated, renderer-compatible previous semantic frame.
- Any interrupted changed-frame transaction must make the next refresh full.
- Keep runtime data JSON-native; do not introduce dataclasses or a general framework.
- Preserve current screen geometry, including the code's 14 px content margin.
- Run commands through `.venv\Scripts\python`.
- Every task ends with a focused test, the full host suite, mpy-cross compilation where device modules changed, and a small commit.

---

### Task 1: Add typed runtime contracts and validated settings

**Files:**
- Create: `src/models.py`
- Create: `tests/test_settings.py`
- Modify: `src/settings.py`
- Modify: `src/config.py`
- Modify: `src/settings.example.json`

**Interfaces:**
- Produces: `models.StopConfig`, `WeatherConfig`, `PowerConfig`, `Settings`, `WifiConfig`, `Departure`, `WeatherReading`, `DisplaySection`, `DisplayStatus`, `DisplayFrame`, `RetainedState`, and `CycleDecision`.
- Produces: `settings.SettingsError` and
  `settings.validate(raw: object) -> Settings`.
- Produces: `settings.load() -> Settings`.
- Produces: `config.ConfigError` and
  `config.load() -> WifiConfig | None`.
- Consumed by: every later task.

- [ ] **Step 1: Write settings validation tests**

Create `tests/test_settings.py` with concrete coverage:

```python
import pytest
import settings


def valid_settings():
    return {
        "stops": [{"name": "Slussen", "site_id": 9192}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "full_refresh_interval_min": 60,
        "power": {"wake_advance_s": 3},
        "weather": {
            "enabled": True,
            "latitude": 59.33,
            "longitude": 18.06,
            "pull_interval_min": 30,
            "max_age_min": 180,
        },
    }


def test_validate_applies_current_defaults():
    cfg = settings.validate({"stops": [{"name": "Slussen", "site_id": 9192}]})
    assert cfg["direction_code"] == 2
    assert cfg["forecast_min"] == 180
    assert cfg["departures_per_stop"] == 3
    assert cfg["full_refresh_interval_min"] == 60
    assert cfg["power"] == {"wake_advance_s": 3}
    assert cfg["weather"] is None


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(stops=[]),
    lambda c: c.update(stops=c["stops"] * 3),
    lambda c: c.update(direction_code=0),
    lambda c: c.update(forecast_min=1201),
    lambda c: c.update(departures_per_stop=0),
    lambda c: c.update(full_refresh_interval_min=0),
    lambda c: c["power"].update(wake_advance_s=60),
])
def test_validate_rejects_invalid_runtime_values(mutate):
    raw = valid_settings()
    mutate(raw)
    with pytest.raises(ValueError):
        settings.validate(raw)


def test_validate_rejects_removed_mode_and_cadence_keys():
    raw = valid_settings()
    raw["power"]["deep_sleep"] = True
    raw["render_interval_min"] = 1
    with pytest.raises(ValueError, match="unsupported setting"):
        settings.validate(raw)


def test_validate_requires_complete_enabled_weather():
    raw = valid_settings()
    del raw["weather"]["longitude"]
    with pytest.raises(ValueError, match="weather.longitude"):
        settings.validate(raw)
```

- [ ] **Step 2: Run the new tests and confirm the validator is missing**

Run:

```text
.venv\Scripts\python -m pytest tests/test_settings.py -v
```

Expected: collection or test failure because `settings.validate` does not exist.

- [ ] **Step 3: Define the contracts without runtime `typing` imports**

In `src/models.py`, place typing imports and `TypedDict` declarations behind
the repository's existing `if False:` pattern. Use these exact field names:

```python
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
```

These declarations stay type-only and add no runtime allocations. Use
`FrameBufferLike` in display functions, `WatchdogLike` in orchestration, and
`ResponseLike` inside the two adapters instead of `Any`.

- [ ] **Step 4: Implement strict normalization**

Define `SettingsError(ValueError)` and `ConfigError(ValueError)`. Implement
`settings.validate()` with explicit helpers such as
`_required_int()`, `_optional_int()`, and `_number()`. Copy input values into a
new normalized dictionary; never return the raw JSON object. Reject:

- a non-dictionary root;
- unknown top-level, `power`, or `weather` keys;
- a stops list outside `1..2` entries, matching the tested screen layouts and
  retained-state budget;
- non-string/blank names or non-positive `site_id`;
- duplicate `(site_id, direction_code)` stop identities;
- direction outside `1..2`;
- forecast outside `1..1200`;
- departures outside `1..3`;
- non-positive full/weather/max-age intervals;
- wake advance outside `0..59`;
- enabled weather without latitude and longitude;
- latitude outside `-90..90` or longitude outside `-180..180`.

Expose `validate(raw: object) -> Settings` and implement its checks through the
named helpers above. Make `load()` exactly:

```python
def load() -> "Settings":
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except OSError:
        raise SettingsError(
            "settings.json missing; copy settings.example.json to settings.json"
        )
    except ValueError as exc:
        raise SettingsError("settings.json is not valid JSON: %s" % exc)
    return validate(raw)
```

Change `config.load()` to validate a root
`{"wifi": {"ssid": "network", "password": "secret"}}`, return `None` for a
missing file or missing Wi-Fi block, and raise a
precise `ConfigError` for malformed JSON or invalid field types. Remove
`config.save()`.

- [ ] **Step 5: Remove obsolete settings from the example**

Delete `data_pull_interval_min`, `render_interval_min`, and
`power.deep_sleep` from `src/settings.example.json`. Keep:

```json
"full_refresh_interval_min": 60,
"power": {
  "wake_advance_s": 3
}
```

- [ ] **Step 6: Run settings tests, then all host checks**

Run:

```text
.venv\Scripts\python -m pytest tests/test_settings.py -v
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/models.py
.venv\Scripts\python -m mpy_cross src/settings.py
.venv\Scripts\python -m mpy_cross src/config.py
```

Expected: all commands pass.

- [ ] **Step 7: Commit the contracts and validation**

```text
git add src/models.py src/settings.py src/config.py src/settings.example.json tests/test_settings.py
git commit -m "refactor: validate runtime configuration"
```

---

### Task 2: Make the display frame explicit and rendering side-effect free

**Files:**
- Create: `tests/test_bitfont.py`
- Modify: `src/models.py`
- Modify: `src/display.py`
- Modify: `src/bitfont.py`
- Modify: `tests/test_display.py`
- Delete: `tests/conftest.py`

**Interfaces:**
- Produces: `display.stop_section(stop_key, name, deps, stale=False) -> DisplaySection`.
- Produces: `display.make_status(kind, reading=None) -> DisplayStatus`.
- Produces: `display.make_frame(sections, footer, status) -> DisplayFrame`.
- Produces: `display.draw_home(fb, frame) -> tuple[int, int]`, with no printing.
- Produces: `display.frame_summary(frame) -> str`.
- Consumed by: retained validation, cycle policy, and `main.py`.

- [ ] **Step 1: Rewrite display tests around the explicit frame**

Add assertions that frames are JSON-native and statuses are discriminated:

```python
def test_frame_is_json_native_and_identifies_stops():
    section = display.stop_section(
        "9192:2", "Slussen",
        [{"line": "474", "destination": "Hemmesta", "display": "4 min"}],
    )
    frame = display.make_frame(
        [section], display.footer_lines("Lor 25 jul", "14:32"),
        display.make_status("wifi_error"),
    )
    assert frame["sections"][0]["stop_key"] == "9192:2"
    assert isinstance(frame["sections"][0]["rows"], list)
    assert frame["status"] == {"kind": "wifi_error"}


def test_frame_summary_reports_only_the_supplied_visible_frame():
    frame = display.make_frame(
        [display.stop_section("9192:2", "Slussen", [], stale=True)],
        ["Lor 25 jul 14:32"],
        {"kind": "weather_error"},
    )
    summary = display.frame_summary(frame)
    assert "SLUSSEN STALE" in summary
    assert "Weather error" in summary
```

Update draw tests to call `display.draw_home(fb, frame)` and assert the actual
Wi-Fi/weather status branch through recorded drawing operations, not only
layout bounds. Remove tests for `section_lines()`.

- [ ] **Step 2: Add the BitFont overflow regression**

Use a tiny temporary `.fnt` fixture whose index claims a glyph larger than
`len(bitfont._GBUF)`. The header/index alone are sufficient because the bound
must be checked before reading bitmap bytes:

```python
import struct


def make_oversized_font(tmp_path):
    header = struct.pack("<4sBBH", b"BFN1", 255, 200, 1)
    index = struct.pack("<HBBI", ord("A"), 40, 40, len(header) + 8)
    path = tmp_path / "oversized.fnt"
    path.write_bytes(header + index)
    return path


def test_draw_rejects_glyph_larger_than_shared_buffer(tmp_path):
    font_path = make_oversized_font(tmp_path)
    font = bitfont.Font(str(font_path))
    with pytest.raises(ValueError, match="glyph bitmap exceeds scratch buffer"):
        font.draw("A", 0, 0, 1, None, lambda *_args: None)
```

- [ ] **Step 3: Run focused tests and confirm failure**

```text
.venv\Scripts\python -m pytest tests/test_display.py tests/test_bitfont.py -v
```

Expected: failures for the new frame/status interfaces and missing glyph bound.

- [ ] **Step 4: Implement the display model**

Use this JSON-native shape:

```python
frame = {
    "sections": [{
        "stop_key": "9192:2",
        "name": "Slussen",
        "hero_main": "4",
        "hero_unit": "min",
        "badge_line": "474",
        "dest": "Hemmesta",
        "rows": [["440", "Slussen", "12 min"]],
        "stale": False,
    }],
    "footer": ["Lor 25 jul 14:32"],
    "status": {
        "kind": "weather",
        "reading": {
            "date": "2026-07-25",
            "condition": "rain",
            "tmin": 12,
            "tmax": 20,
            "precip": 40,
        },
    },
}
```

Allowed status forms are:

```python
{"kind": "none"}
{"kind": "wifi_error"}
{"kind": "weather_error"}
{"kind": "weather", "reading": reading}
```

`stop_section()` must include `stop_key` and build row values as lists, not
tuples. `draw_home()` receives the whole frame and selects the footer branch
using `status["kind"]`. Remove `WEATHER_ERROR`, `WIFI_ERROR`, and
`section_lines()`.

Move the existing serial string construction into `frame_summary()`. Printing
will happen once in `main.py`, after the new frame has been rendered and
selected for display.

- [ ] **Step 5: Bound BitFont scratch use**

Immediately after calculating a glyph's row-byte count and bitmap size in
`Font.draw()`, add:

```python
nbytes = row_bytes * self.height
if nbytes > len(_GBUF):
    raise ValueError(
        "glyph bitmap exceeds scratch buffer: %d > %d" % (nbytes, len(_GBUF))
    )
```

Keep the shared buffer and file streaming strategy unchanged.

- [ ] **Step 6: Delete the obsolete host framebuf shim**

Delete `tests/conftest.py`; `display.py` no longer imports MicroPython
`framebuf`. Update stale test names and comments that mention the removed
built-in scaled font.

- [ ] **Step 7: Run all checks and compile changed device modules**

```text
.venv\Scripts\python -m pytest tests/test_display.py tests/test_bitfont.py -v
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/display.py
.venv\Scripts\python -m mpy_cross src/bitfont.py
```

Expected: all commands pass.

- [ ] **Step 8: Commit the explicit display frame**

```text
git add src/models.py src/display.py src/bitfont.py tests/test_display.py tests/test_bitfont.py tests/conftest.py
git commit -m "refactor: make display frames explicit"
```

---

### Task 3: Strictly validate retained state and configuration compatibility

**Files:**
- Modify: `src/models.py`
- Modify: `src/retained.py`
- Modify: `tests/test_retained.py`

**Interfaces:**
- Produces: `retained.RETAINED_VERSION = 2`.
- Produces: `retained.RENDER_REVISION = 1`.
- Produces: `retained.settings_fingerprint(cfg: Settings) -> str`.
- Produces: `retained.encode(state: RetainedState) -> bytes`.
- Produces:
  `retained.decode(raw, expected_fingerprint, expected_stop_keys) -> RetainedState | None`.
- Consumed by: `cycle.py` and `main.py`.

- [ ] **Step 1: Replace the retained fixture with the new schema**

Use a fixture containing:

```python
def state(fingerprint="a1b2c3d4"):
    return {
        "v": retained.RETAINED_VERSION,
        "render_rev": retained.RENDER_REVISION,
        "settings": fingerprint,
        "frame": {
            "sections": [{
                "stop_key": "9192:2", "name": "Slussen",
                "hero_main": "4", "hero_unit": "min",
                "badge_line": "474", "dest": "Hemmesta",
                "rows": [["440", "Slussen", "12 min"]],
                "stale": False,
            }],
            "footer": ["Lor 25 jul 14:32"],
            "status": {"kind": "none"},
        },
        "last_full": 123,
        "weather": None,
        "weather_time": None,
        "last_ntp": 120,
    }
```

Add parametrized rejection tests for a missing frame key, invalid status kind,
wrong row length, non-integer timestamps, wrong renderer revision, wrong
fingerprint, and a section count/identity not matching the supplied expected
stop keys. Preserve corruption and maximum-size tests.

- [ ] **Step 2: Add deterministic fingerprint tests**

```python
def test_settings_fingerprint_changes_with_rendered_identity(valid_cfg):
    original = retained.settings_fingerprint(valid_cfg)
    changed = copy.deepcopy(valid_cfg)
    changed["stops"][0]["name"] = "Other label"
    assert retained.settings_fingerprint(changed) != original


def test_settings_fingerprint_ignores_no_runtime_objects(valid_cfg):
    assert retained.settings_fingerprint(valid_cfg) == retained.settings_fingerprint(valid_cfg)
```

The fingerprint input must include ordered stop names/site IDs, direction,
departure count, full-refresh interval, and normalized weather configuration.

- [ ] **Step 3: Run retained tests and confirm schema failures**

```text
.venv\Scripts\python -m pytest tests/test_retained.py -v
```

Expected: failures because decode only checks the envelope and version.

- [ ] **Step 4: Implement semantic decoding**

Keep the checksum envelope but compact JSON with:

```python
body = json.dumps(state, separators=(",", ":")).encode("utf-8")
```

Implement small validators `_is_int_or_none`, `_valid_reading`,
`_valid_status`, `_valid_section`, `_valid_frame`, and `_valid_state`.
`decode()` returns `None` for every invalid nested field and accepts an
expected fingerprint plus the ordered expected stop keys:

After the existing envelope parsing and `json.loads`, apply this compatibility
block before returning:

```python
if not isinstance(state, dict):
    return None
if state.get("v") != RETAINED_VERSION:
    return None
if state.get("render_rev") != RENDER_REVISION:
    return None
if state.get("settings") != expected_fingerprint:
    return None
if not _valid_state(state):
    return None
sections = state["frame"]["sections"]
if [section["stop_key"] for section in sections] != expected_stop_keys:
    return None
return state
```

Use a deterministic 32-bit checksum over a canonical JSON list of normalized
settings and format it as eight lowercase hexadecimal characters. This is a
compatibility token, not authentication.

- [ ] **Step 5: Run retained and full checks**

```text
.venv\Scripts\python -m pytest tests/test_retained.py -v
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/retained.py
```

Expected: all commands pass and the representative retained fixture remains
below `retained.MAX_BYTES`.

- [ ] **Step 6: Commit semantic retained-state validation**

```text
git add src/models.py src/retained.py tests/test_retained.py
git commit -m "fix: validate retained display state"
```

---

### Task 4: Extract and characterize the pure wake-cycle policy

**Files:**
- Create: `src/cycle.py`
- Create: `tests/test_cycle.py`
- Modify: `src/models.py`
- Modify: `src/weather.py`
- Modify: `tests/test_weather.py`
- Modify: `src/main.py`

**Interfaces:**
- Produces: `cycle.stop_key(stop, direction_code) -> str`.
- Produces: `cycle.weather_due(now_epoch, last_weather_time, interval_s) -> bool`.
- Produces:
  `cycle.decide(settings, previous, departure_results, connected, weather_attempted, weather_result, now_epoch, today_iso, footer, last_ntp_epoch) -> CycleDecision`.
- `CycleDecision` contains `frame`, `refresh` (`"none"`, `"partial"`, or `"full"`), and `state`.
- Consumed by: the hardware orchestrator in Tasks 6–7.

- [ ] **Step 1: Write the core departure and refresh tests**

Create `tests/test_cycle.py` with helpers for normalized settings and retained
state. Cover:

```python
def test_successful_empty_departures_are_fresh():
    decision = decide(departure_results=[[]])
    section = decision["frame"]["sections"][0]
    assert section["dest"] == "No departures"
    assert section["stale"] is False


def test_failed_stop_reuses_only_matching_stop_identity():
    previous = retained_with_section(stop_key="9192:2", destination="Old")
    decision = decide(previous=previous, departure_results=[None])
    assert decision["frame"]["sections"][0]["dest"] == "Old"
    assert decision["frame"]["sections"][0]["stale"] is True


def test_failed_reconfigured_stop_does_not_reuse_old_departures():
    previous = retained_with_section(stop_key="9192:2", destination="Old")
    cfg = settings_with_stop(site_id=1234)
    decision = decide(settings=cfg, previous=previous, departure_results=[None])
    assert decision["frame"]["sections"][0]["dest"] == "No departures"
    assert decision["refresh"] == "full"


def test_refresh_selection_is_none_partial_then_full():
    assert decide(previous=same_frame_state())["refresh"] == "none"
    assert decide(previous=recent_compatible_state(), now_epoch=200)["refresh"] == "partial"
    assert decide(previous=old_full_state(), now_epoch=4000)["refresh"] == "full"
```

- [ ] **Step 2: Write weather and offline policy tests**

Cover weather success, valid same-day fallback, expired fallback, previous-day
rejection, disabled weather clearing, and status priority:

```python
def test_disabling_weather_clears_retained_reading():
    previous = retained_with_weather()
    decision = decide(settings=settings_without_weather(), previous=previous)
    assert decision["frame"]["status"] == {"kind": "none"}
    assert decision["state"]["weather"] is None
    assert decision["state"]["weather_time"] is None


def test_wifi_error_has_priority_over_weather_error():
    decision = decide(connected=False, departure_results=[None])
    assert decision["frame"]["status"] == {"kind": "wifi_error"}


def test_future_weather_timestamp_is_due_and_not_fresh():
    assert cycle.weather_due(100, 200, 1800) is True
    assert weather.keep_last_good(reading_today(), "2026-07-25", -100, 10800) is False
```

- [ ] **Step 3: Run the new policy tests and confirm the module is absent**

```text
.venv\Scripts\python -m pytest tests/test_cycle.py tests/test_weather.py -v
```

Expected: import failure for `cycle` and a failure for negative weather age.

- [ ] **Step 4: Implement the pure decision function**

Use exact inputs rather than reading clocks or hardware inside `cycle.py`.
`departure_results` is ordered with settings stops, where `None` means fetch
failure and an empty list means successful no departures.

Rules:

- Build a fresh section for every non-`None` result.
- On `None`, reuse only the previous section whose `stop_key` exactly matches.
- Otherwise build stale `No departures`.
- When disconnected, select `wifi_error`.
- When weather is disabled, clear retained weather and select `none`.
- When a weather attempt succeeds, retain it and its supplied `now_epoch`.
- When it fails or is not attempted, keep prior weather only when
  `weather.keep_last_good()` accepts it.
- Carry the `last_ntp_epoch` supplied by orchestration into the proposed
  retained state; the policy module never invokes NTP itself.
- Select `weather_error` only when weather is enabled, Wi-Fi is available, and
  no valid reading exists.
- Compare the complete new frame with the previous frame.
- Select full when no compatible previous frame exists or
  `now_epoch - last_full >= full_refresh_interval_min * 60`.
- Update `last_full` in the proposed state when the selected refresh is full.

Remove `weather_bucket` from retained state. `weather_due()` uses elapsed
seconds; `last_weather_time is None`, negative age, or age at least the
configured interval is due.

- [ ] **Step 5: Integrate policy without deleting the legacy branch yet**

Change `deep_sleep_cycle()` to gather I/O results, call `cycle.decide()`, and
use its frame/refresh/state. Keep `display_loop()` temporarily so this commit
isolates policy extraction from architectural deletion.

Print only:

```python
print(display.frame_summary(decision["frame"]))
```

for the new frame, never while reconstructing the previous frame.

- [ ] **Step 6: Run all checks and compile**

```text
.venv\Scripts\python -m pytest tests/test_cycle.py tests/test_weather.py -v
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/cycle.py
.venv\Scripts\python -m mpy_cross src/weather.py
```

Expected: all commands pass.

- [ ] **Step 7: Commit the pure cycle policy**

```text
git add src/models.py src/cycle.py src/main.py src/weather.py tests/test_cycle.py tests/test_weather.py
git commit -m "refactor: extract deep sleep cycle policy"
```

---

### Task 5: Make API adapters single-attempt and failure-honest

**Files:**
- Create: `tests/test_sl.py`
- Create: `tests/test_openmeteo.py`
- Modify: `src/sl.py`
- Modify: `src/openmeteo.py`
- Modify: `src/departures.py`
- Modify: `src/weather.py`
- Modify: `src/main.py`

**Interfaces:**
- Produces: `sl.fetch_departures(site_id, transport="BUS", forecast=60, direction=None, timeout_s=10) -> dict`.
- Produces: `openmeteo.fetch_today(latitude, longitude, timeout_s=10) -> dict`.
- Removes: all `retries` parameters and retry delays.

- [ ] **Step 1: Add fake-response adapter tests**

Monkeypatch each module's `requests.get` with a response carrying
`status_code`, a `.json()` result/error, and a `.close()` flag. Assert:

```python
def test_non_2xx_is_failure_and_response_is_closed(monkeypatch):
    response = FakeResponse(429, {"reason": "rate limited"})
    monkeypatch.setattr(sl.requests, "get", lambda *a, **k: response)
    with pytest.raises(OSError, match="HTTP 429"):
        sl.fetch_departures(9192)
    assert response.closed


def test_departures_requires_top_level_departures(monkeypatch):
    response = FakeResponse(200, {"error": "unexpected"})
    monkeypatch.setattr(sl.requests, "get", lambda *a, **k: response)
    with pytest.raises(ValueError, match="departures"):
        sl.fetch_departures(9192)
    assert response.closed


def test_legitimate_empty_departures_is_success(monkeypatch):
    response = FakeResponse(200, {"departures": []})
    monkeypatch.setattr(sl.requests, "get", lambda *a, **k: response)
    assert sl.fetch_departures(9192) == {"departures": []}
```

Mirror the tests for Open-Meteo's required `daily` and `hourly` dictionaries,
including invalid JSON and response closure.

- [ ] **Step 2: Run adapter tests and confirm current behavior fails**

```text
.venv\Scripts\python -m pytest tests/test_sl.py tests/test_openmeteo.py -v
```

Expected: non-2xx and malformed payload tests fail because current adapters
accept any JSON body.

- [ ] **Step 3: Implement one-attempt adapters**

The SL adapter uses:

```python
gc.collect()
response = requests.get(url, timeout=timeout_s)
try:
    status = getattr(response, "status_code", 200)
    if status < 200 or status >= 300:
        raise OSError("HTTP %d" % status)
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("departures"), list):
        raise ValueError("response missing valid departures list")
    return payload
finally:
    response.close()
```

The Open-Meteo adapter uses the same status/close structure but validates:

```python
if (
    not isinstance(payload, dict)
    or not isinstance(payload.get("daily"), dict)
    or not isinstance(payload.get("hourly"), dict)
):
    raise ValueError("response missing valid daily/hourly data")
```

Remove `time`, `RETRY_DELAY_S`, loops, attempt logging, and `retries`. Keep
plain HTTP and the 10-second per-request timeout.

Update `_fetch_all_stops()` and weather fetching in `main.py` to call these
signatures. Keep per-stop exception isolation.

- [ ] **Step 4: Run adapter and full checks**

```text
.venv\Scripts\python -m pytest tests/test_sl.py tests/test_openmeteo.py tests/test_departures.py tests/test_weather.py -v
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/sl.py
.venv\Scripts\python -m mpy_cross src/openmeteo.py
```

Expected: all commands pass.

- [ ] **Step 5: Commit the network boundary**

```text
git add src/sl.py src/openmeteo.py src/departures.py src/weather.py src/main.py tests/test_sl.py tests/test_openmeteo.py
git commit -m "fix: reject failed API responses"
```

---

### Task 6: Make retained-state persistence a safe panel transaction

**Files:**
- Create: `src/refresh_txn.py`
- Create: `tests/test_refresh_txn.py`
- Modify: `src/main.py`
- Modify: `tests/test_retained.py`

**Interfaces:**
- Produces:
  `refresh_txn.apply(state, encode, invalidate, refresh, commit) -> None`.
- Produces hardware adapters in `main.py`: `_rtc_invalidate()`,
  `_rtc_commit(raw, expected_fingerprint)`, and `_rtc_load(expected_fingerprint)`.
- Consumes: encoded `RetainedState` from Task 3 and `CycleDecision` from Task 4.

- [ ] **Step 1: Test transaction ordering and failure behavior**

Create:

```python
import pytest
import refresh_txn


def test_changed_refresh_invalidates_then_refreshes_then_commits():
    events = []
    refresh_txn.apply(
        {"frame": "new"},
        lambda state: events.append(("encode", state)) or b"new",
        lambda: events.append("invalidate"),
        lambda: events.append("refresh"),
        lambda raw: events.append(("commit", raw)),
    )
    assert events == [
        ("encode", {"frame": "new"}),
        "invalidate",
        "refresh",
        ("commit", b"new"),
    ]


def test_refresh_failure_never_commits_new_state():
    events = []
    def fail():
        events.append("refresh")
        raise OSError("panel")
    with pytest.raises(OSError, match="panel"):
        refresh_txn.apply(
            {}, lambda state: b"new",
            lambda: events.append("invalidate"), fail,
            lambda raw: events.append(("commit", raw)),
        )
    assert events == ["invalidate", "refresh"]


def test_encode_failure_happens_before_invalidation():
    events = []
    def fail_encode(state):
        events.append("encode")
        raise ValueError("oversize")
    with pytest.raises(ValueError, match="oversize"):
        refresh_txn.apply(
            {}, fail_encode, lambda: events.append("invalidate"),
            lambda: events.append("refresh"),
            lambda raw: events.append("commit"),
        )
    assert events == ["encode"]
```

- [ ] **Step 2: Run tests and confirm the transaction module is absent**

```text
.venv\Scripts\python -m pytest tests/test_refresh_txn.py -v
```

Expected: import failure for `refresh_txn`.

- [ ] **Step 3: Implement the minimal transaction helper**

```python
def apply(
    state: "RetainedState",
    encode: "Callable[[RetainedState], bytes]",
    invalidate: "Callable[[], None]",
    refresh: "Callable[[], None]",
    commit: "Callable[[bytes], None]",
) -> None:
    encoded_state = encode(state)
    invalidate()
    refresh()
    commit(encoded_state)
```

The value of this module is its enforced order and host-testable boundary; do
not add classes or rollback logic.

- [ ] **Step 4: Integrate the transaction**

In `main.py`:

- compute `decision`;
- for `partial`/`full`, call `refresh_txn.apply()` with the proposed state,
  `retained.encode`, and:
  - `_rtc_invalidate`, which writes `b""` and verifies the readback is empty;
  - a closure/function that calls `_draw_and_refresh()` using the previous
    validated frame retained in RAM;
  - `_rtc_commit`, which writes exact bytes, checks exact readback, and calls
    `retained.decode(readback, fingerprint, stop_keys)`;
- for `none`, commit the encoded state directly without invalidating or
  touching the panel.

The new semantic frame is logged once before the selected refresh. The old
frame is reconstructed silently for `0x10`.

- [ ] **Step 5: Run host checks and compile**

```text
.venv\Scripts\python -m pytest tests/test_refresh_txn.py tests/test_retained.py tests/test_cycle.py -v
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/refresh_txn.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit the transaction**

```text
git add src/refresh_txn.py src/main.py tests/test_refresh_txn.py tests/test_retained.py
git commit -m "fix: make panel refresh state transactional"
```

- [ ] **Step 7: Hardware checkpoint 1**

Run:

```text
deploy.bat
mpremote connect auto repl
```

Verify through serial and owner observation:

1. a normal changed wake uses partial refresh when compatible retained state
   exists;
2. a second changed wake also uses partial;
3. clear RTC memory once with a one-off `mpremote run` expression/tool;
4. the next changed wake performs a full refresh;
5. the following changed wake returns to partial;
6. the panel sleeps after every refresh.

Do not begin Task 7 until this checkpoint passes.

---

### Task 7: Delete the awake runtime and setup portal, then make boot synchronous

**Files:**
- Modify: `src/main.py`
- Modify: `src/wifi.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Delete: `src/server.py`
- Delete: `src/lib/microdot.py`
- Delete: `typings/asyncio.pyi`

**Interfaces:**
- Produces: synchronous `main() -> None`.
- Produces: synchronous `_wait_until_epoch(wdt, target_epoch) -> None`.
- Keeps: `wifi.connect_sta()` only.
- Removes: `display_loop`, continuous tick helpers, mode branching, AP/setup,
  reconnect, and asyncio.

- [ ] **Step 1: Add a source-level architecture guard**

Create a small test in `tests/test_architecture.py`:

```python
from pathlib import Path


def test_removed_runtime_is_absent():
    source = Path("src/main.py").read_text(encoding="utf-8")
    for obsolete in (
        "display_loop", "asyncio", "_seconds_to_next_tick",
        "_sleep_until_next_tick", "_WIFI_RECONNECT_AFTER_FAILS",
        'get("deep_sleep"',
    ):
        assert obsolete not in source


def test_setup_portal_sources_are_absent():
    assert not Path("src/server.py").exists()
    assert not Path("src/lib/microdot.py").exists()
```

- [ ] **Step 2: Run the guard and confirm it fails**

```text
.venv\Scripts\python -m pytest tests/test_architecture.py -v
```

Expected: failure listing the still-present legacy symbols and files.

- [ ] **Step 3: Reduce `main.py` to one synchronous path**

Delete:

- `display_loop()`;
- `_seconds_to_next_tick()` and `_sleep_until_next_tick()`;
- `_WIFI_RECONNECT_AFTER_FAILS` and `_WDT_FEED_CHUNK_S`;
- every branch on `power.deep_sleep`;
- the awake-only NTP, weather, font warming, reconnect, and render policies;
- the `wifi_cfg` argument passed through the cycle but never used;
- the `asyncio` import and `async`/`await` keywords.

Implement boundary wait with bounded ordinary sleep:

```python
def _wait_until_epoch(wdt: "Any", target_epoch: int) -> None:
    while True:
        remaining = target_epoch - time.time()
        if remaining <= 0:
            return
        wdt.feed()
        time.sleep(remaining if remaining < 1 else 1)
```

The single `main()`:

1. records boot ticks;
2. validates settings and credentials;
3. calculates settings fingerprint and decodes RTC state;
4. attempts STA connection;
5. NTP-syncs a cold boot when connected;
6. calculates/recalculates the request boundary;
7. runs one synchronous wake cycle;
8. disables WLAN and calls deep sleep.

Missing credentials and connection failure still flow through the cycle with
`connected=False`, producing the on-screen Wi-Fi status.

- [ ] **Step 4: Make fatal recovery explicit**

Keep `KeyboardInterrupt` recoverable. Distinguish validation/configuration
errors from unexpected runtime faults:

```python
try:
    main()
except KeyboardInterrupt:
    raise
except (settings.SettingsError, config.ConfigError) as exc:
    print("main: configuration/startup error:")
    sys.print_exception(exc)
    while True:
        time.sleep(1)
except Exception as exc:
    print("main: unexpected runtime error; retained state invalidated")
    sys.print_exception(exc)
    try:
        machine.RTC().memory(b"")
    except Exception:
        pass
    machine.deepsleep(60_000)
```

Inside the wake cycle, continue using `_safe_sleep()` around every panel
operation. Do not broaden the configuration exception branch to swallow panel
or network runtime defects intentionally raised after orchestration begins.

- [ ] **Step 5: Remove the portal and reconnect code**

Delete `src/server.py`, `src/lib/microdot.py`, and `typings/asyncio.pyi`.
Reduce `wifi.py` to `connect_sta()` and its constants. Remove `AP_SSID`,
`start_ap()`, and `reconnect()`.

Remove the Microdot override and `src/lib` exclusion from `pyproject.toml`.
Remove the `!src/lib/`, `!src/lib/**`, and `src/lib/*.mpy` exceptions from
`.gitignore`.

- [ ] **Step 6: Run all checks and compile every surviving device module**

```text
.venv\Scripts\python -m pytest tests/test_architecture.py -v
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/bitfont.py
.venv\Scripts\python -m mpy_cross src/config.py
.venv\Scripts\python -m mpy_cross src/cycle.py
.venv\Scripts\python -m mpy_cross src/departures.py
.venv\Scripts\python -m mpy_cross src/display.py
.venv\Scripts\python -m mpy_cross src/epd7in5v2.py
.venv\Scripts\python -m mpy_cross src/localtime.py
.venv\Scripts\python -m mpy_cross src/models.py
.venv\Scripts\python -m mpy_cross src/openmeteo.py
.venv\Scripts\python -m mpy_cross src/refresh_txn.py
.venv\Scripts\python -m mpy_cross src/retained.py
.venv\Scripts\python -m mpy_cross src/settings.py
.venv\Scripts\python -m mpy_cross src/sl.py
.venv\Scripts\python -m mpy_cross src/wake_schedule.py
.venv\Scripts\python -m mpy_cross src/weather.py
.venv\Scripts\python -m mpy_cross src/wifi.py
```

Expected: all commands pass. `src/main.py` remains source because MicroPython
auto-runs `main.py`.

- [ ] **Step 7: Commit the single runtime**

```text
git add -A src typings pyproject.toml .gitignore tests/test_architecture.py
git commit -m "refactor: keep only the deep sleep runtime"
```

- [ ] **Step 8: Hardware checkpoint 2**

Deploy the current tree and observe at least two minute wakes. Confirm:

- request start remains aligned to `:00`;
- wake preparation is reported relative to the configured advance;
- normal changed frames remain partial after the first compatible wake;
- Wi-Fi failure appears on-screen and the board sleeps for another retry;
- Ctrl-C remains usable for a genuine configuration error;
- serial reports only the final visible screen.

Do not begin deployment cleanup until this checkpoint passes.

---

### Task 8: Make deployment deterministic and retire one-off tools

**Files:**
- Modify: `deploy.bat`
- Modify: `.gitignore`
- Modify: `tools/test_pattern.py`
- Modify: `tools/calibration_guide.py`
- Modify: `tools/test_new_layout.py`
- Delete: `tools/diag_mem.py`
- Delete: `tools/test_bitfont_device.py`
- Delete: `tools/gen_font_check.py` after moving its durable assertions
- Modify: `tests/test_bitfont.py`

**Interfaces:**
- Produces: explicit deploy module list in `deploy.bat`.
- Produces: best-effort one-time device deletion of retired portal files.
- Keeps: font generation, previews, calibration, test pattern, and layout
  hardware tools.

- [ ] **Step 1: Move font-file invariants into pytest**

Transfer the exact useful checks from `tools/gen_font_check.py` into
`tests/test_bitfont.py`: valid magic, sorted unique codepoints, offsets within
the file, bitmap sizes fitting `_GBUF`, and required deployed charset coverage.
Run:

```text
.venv\Scripts\python -m pytest tests/test_bitfont.py -v
```

Expected: pass before deleting the standalone checker.

- [ ] **Step 2: Replace wildcard deployment**

In `deploy.bat`, declare the source modules explicitly:

```bat
set "MODULES=bitfont config cycle departures display epd7in5v2 localtime models openmeteo refresh_txn retained settings sl wake_schedule weather wifi"
```

Compile and copy only those names:

```bat
for %%M in (%MODULES%) do (
    echo   compile %%M.mpy
    "%PY%" -m mpy_cross "%SRCDIR%\%%M.py"
    if errorlevel 1 goto :fail
)

for %%M in (%MODULES%) do (
    echo   cp %%M.mpy
    %MP% %CONN% fs cp "%SRCDIR%\%%M.mpy" ":%%M.mpy"
    if errorlevel 1 goto :fail
)
```

Copy `main.py`, `settings.json`, and `config.json` explicitly. A missing local
`config.json` must print that existing device credentials are preserved; it
must not delete them.

Remove every `src/lib` compile/copy block. Before copying, best-effort remove:

```bat
%MP% %CONN% fs rm :server.mpy >nul 2>nul
%MP% %CONN% fs rm :lib/microdot.mpy >nul 2>nul
%MP% %CONN% fs rmdir :lib >nul 2>nul
```

Do not make an absent retired file fail deployment.

- [ ] **Step 3: Remove obsolete tools and harden retained hardware tools**

Delete `diag_mem.py`, `test_bitfont_device.py`, and `gen_font_check.py`.
For each remaining tool that wakes the panel, put all work after init inside:

```python
epd = EPD7in5V2()
try:
    epd.init()
    # draw and display exactly once
finally:
    epd.sleep()
```

Remove any redundant `clear()` immediately before a complete framebuffer
display.

- [ ] **Step 4: Run deterministic build checks**

```text
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
deploy.bat
```

Expected: host checks pass; deployment compiles/copies only the explicit
module set; retired device files are absent afterward.

- [ ] **Step 5: Commit deployment and tool cleanup**

```text
git add -A deploy.bat .gitignore tools tests/test_bitfont.py
git commit -m "chore: make firmware deployment deterministic"
```

- [ ] **Step 6: Hardware checkpoint 3**

After deployment, list device files and verify no `server.mpy`, `lib`, or
unlisted stale module remains. Observe two wakes and repeat the deliberate
retained-incompatibility full-then-partial sequence.

---

### Task 9: Rewrite documentation around the finished product

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `src/main.py`
- Modify: `src/bitfont.py`
- Modify: `src/sl.py`
- Modify: `src/openmeteo.py`
- Modify: `src/config.py`
- Modify: `src/settings.py`
- Modify: `deploy.bat`
- Preserve: `CLAUDE.md`

**Interfaces:**
- Produces: one current architecture description matching the code and
  deployment.

- [ ] **Step 1: Update the architecture and operation docs**

In `AGENTS.md` and `README.md`, replace references to:

- a single asyncio event loop;
- `display_loop`;
- awake Wi-Fi reconnect;
- AP setup/Microdot;
- separate data/render intervals;
- optional deep sleep;
- vendored `src/lib`;
- repeated per-wake retries.

Document the actual single flow:

```text
boot -> validate settings/config -> restore compatible semantic state
-> connect -> wait until :00 -> fetch once -> decide refresh
-> invalidate/refresh/commit when changed -> deep sleep
```

Record the renderer revision/settings fingerprint rule and the
invalidate-before-refresh transaction. Keep the one-minute cadence,
3-second configurable wake advance, daily NTP behavior, visible Wi-Fi state,
single framebuffer, and full/partial policy.

- [ ] **Step 2: Remove stale implementation history from runtime comments**

Rewrite module headers and comments to explain current invariants. Condense
resolved TLS history to the durable reason for plain HTTP and host
precompilation. Remove claims that:

- the framebuffer lives in `display_loop`;
- font caches are warmed by an awake boot path;
- TLS handshakes drive current retry/WDT math;
- only settings are gitignored;
- setup server/lib still exist.

Do not alter EPD driver command logic. In its comments only, replace any
obsolete “empirical/unknown polarity” language with the hardware-confirmed
contract.

- [ ] **Step 3: Reconcile screen documentation**

Describe the current streamed Bitter font roles, left-aligned hero layout, and
`CONTENT_MARGIN = 14`. Remove old `HERO_SCALE`, `CAPTION_SCALE`, centered
layout, and 25 px margin claims. Keep the no-border rule and current footer
design.

- [ ] **Step 4: Check docs for removed symbols and run all verification**

```text
rg -n "display_loop|Microdot|start_ap|reconnect|render_interval_min|data_pull_interval_min|power\\.deep_sleep|Single asyncio" AGENTS.md README.md src deploy.bat
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
```

Expected: the search finds no current-runtime references; any intentionally
retained historical mention is clearly labeled and justified. Tests and mypy
pass.

- [ ] **Step 5: Commit documentation**

```text
git add AGENTS.md README.md src deploy.bat
git commit -m "docs: describe the deep sleep only firmware"
```

- [ ] **Step 6: Final verification**

Run:

```text
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
deploy.bat
mpremote connect auto repl
```

Verify:

1. all host tests pass;
2. mypy reports no issues;
3. every declared module compiles;
4. deployment contains only intended source, bytecode, configuration, and
   font files;
5. two consecutive changed wakes use partial refresh after a valid retained
   frame exists;
6. one renderer/settings incompatibility causes exactly one full refresh;
7. the following changed wake returns to partial;
8. unavailable Wi-Fi is shown and automatically retried;
9. serial content matches the visible screen;
10. the panel is put to sleep after every refresh.

The cleanup is complete only after the owner confirms the physical display.
