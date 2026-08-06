# Deep-Sleep Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the existing `main` deep-sleep path so every validly configured wake either presents honest current/error state and schedules the next wake, or enters bounded automatic recovery without risking an invalid differential refresh.

**Architecture:** Preserve the current dual-mode runtime, display representation, configuration format, and EPD driver. The runtime lives in host-precompiled `app.py`; tiny source `main.py` remains the activation-last MicroPython entrypoint and imports `app`. Add validation at existing boundaries, make RTC persistence transactional around changed panel frames, establish the hardware-confirmed WDT -> Wi-Fi -> framebuffer setup order only in the deep-sleep path, and keep all expected network failures inside the current stale/error display policy.

> Hardware correction (2026-08-06): the initial plan below placed the
> framebuffer before Wi-Fi. COM3 disproved that order: it left 4/10 expected RX
> buffers, logged `0x3001`, and raised `WiFi Out of Memory`. The implemented and
> regression-tested order is WDT -> Wi-Fi STA -> framebuffer -> RTC/NTP/API/EPD.

**Tech Stack:** MicroPython v1.28.0 on ESP32_GENERIC, CPython 3.12 host tests with pytest, mypy, `mpy-cross`, Windows `deploy.bat`, and `mpremote` on COM3.

## Global Constraints

- Work only on `codex/deep-sleep-reliability`, branched directly from `main`; do not merge or cherry-pick `codex/deep-sleep-cleanup`.
- Preserve the awake fallback, setup server, Microdot, asyncio, current screen design, existing settings keys, and one-minute cadence.
- Do not change EPD command bytes, polarity, pin mapping, BUSY polling, or panel sleep sequencing.
- Allocate exactly one 48,000-byte framebuffer per deep-sleep wake and reuse it for both differential planes.
- Keep one API attempt per source per deep-sleep wake and plain HTTP for both public, keyless endpoints.
- Runtime annotations use built-ins and quoted expressions; typing-only imports stay under `if False:`.
- Every changed first-party module must pass mypy and `mpy-cross`; `app.py` ships as bytecode while the tiny `main.py` activation shim remains source on the device.
- Do not merge to `main` until COM3 serial verification and owner visual confirmation pass.

---

### Task 1: Validate existing configuration without changing its shape

**Files:**
- Create: `tests/test_settings.py`
- Modify: `src/settings.py`
- Modify: `src/config.py`

**Interfaces:**
- Produces `settings.SettingsError(ValueError)`.
- Produces `settings.validate(raw: object) -> dict[str, Any]` while preserving legacy keys and defaults.
- Produces `config.ConfigError(ValueError)` and `config.validate(raw: object) -> dict[str, Any]`.
- Keeps `settings.load()` and `config.load()` return shapes consumed by current `app.py` and `server.py`.

- [ ] **Step 1: Write failing settings/configuration tests**

Create `tests/test_settings.py` with a complete valid fixture and focused mutations:

```python
import json
import pytest

import config
import settings


def valid_settings():
    return {
        "stops": [{"name": "Slussen", "site_id": 9192}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 60,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
        "weather": {
            "enabled": True,
            "latitude": 59.33,
            "longitude": 18.06,
            "pull_interval_min": 30,
            "max_age_min": 180,
        },
    }


def test_validate_preserves_existing_settings_shape_and_defaults():
    raw = valid_settings()
    assert settings.validate(raw) == raw
    minimal = {"stops": [{"name": "Slussen", "site_id": 9192}]}
    validated = settings.validate(minimal)
    assert validated["direction_code"] == 2
    assert validated["forecast_min"] == 180
    assert validated["departures_per_stop"] == 3
    assert validated["data_pull_interval_min"] == 1
    assert validated["render_interval_min"] == 1
    assert validated["full_refresh_interval_min"] == 30
    assert validated["power"] == {"deep_sleep": False, "wake_advance_s": 3}


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(stops=[]),
    lambda x: x["stops"][0].update(name=""),
    lambda x: x["stops"][0].update(site_id=0),
    lambda x: x.update(direction_code=3),
    lambda x: x.update(forecast_min=1201),
    lambda x: x.update(departures_per_stop=4),
    lambda x: x.update(render_interval_min=0),
    lambda x: x["power"].update(deep_sleep="yes"),
    lambda x: x["power"].update(wake_advance_s=60),
    lambda x: x["weather"].update(latitude=91),
])
def test_validate_rejects_values_that_can_break_runtime(mutate):
    raw = valid_settings()
    mutate(raw)
    with pytest.raises(settings.SettingsError):
        settings.validate(raw)


def test_config_accepts_existing_nested_wifi_and_missing_file(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"wifi": {"ssid": "home", "password": "secret"}}))
    monkeypatch.setattr(config, "PATH", str(path))
    assert config.load() == {"wifi": {"ssid": "home", "password": "secret"}}
    monkeypatch.setattr(config, "PATH", str(tmp_path / "missing.json"))
    assert config.load() == {}


@pytest.mark.parametrize("raw", [[], {"wifi": []}, {"wifi": {"ssid": 7}}, {"wifi": {"ssid": "x", "password": 7}}])
def test_config_rejects_malformed_credentials(raw):
    with pytest.raises(config.ConfigError):
        config.validate(raw)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```text
.venv\Scripts\python -m pytest tests/test_settings.py -v
```

Expected: collection succeeds and tests fail because `validate`, `SettingsError`, and `ConfigError` do not exist.

- [ ] **Step 3: Implement minimal backward-compatible validation**

In `settings.py`, copy the input dictionaries/lists, apply the current runtime defaults, and validate exactly the ranges in the tests plus weather longitude `-180..180`, positive weather intervals, and at most two stops. Do not reject unknown legacy keys. `load()` must translate missing/invalid JSON into `SettingsError` and return `validate(json.load(f))`.

In `config.py`, preserve `save()`, make missing files return `{}`, and validate only the existing optional nested `wifi` mapping. Default a missing password to `""`; require an SSID string when the `wifi` key exists.

- [ ] **Step 4: Verify GREEN and compatibility**

Run:

```text
.venv\Scripts\python -m pytest tests/test_settings.py -v
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy src/settings.py src/config.py
.venv\Scripts\python -m mpy_cross src/settings.py
.venv\Scripts\python -m mpy_cross src/config.py
```

Expected: all commands exit 0; the full suite remains at least 62 tests plus the new cases.

- [ ] **Step 5: Commit the validated boundary**

```text
git add src/settings.py src/config.py tests/test_settings.py
git commit -m "fix: validate runtime configuration safely"
```

---

### Task 2: Make retained state strict and settings-compatible

**Files:**
- Modify: `src/retained.py`
- Modify: `src/app.py`
- Modify: `tests/test_retained.py`

**Interfaces:**
- Produces `retained.STATE_VERSION = 2` and `retained.RENDER_REVISION = 1`.
- Produces `retained.settings_fingerprint(cfg: dict[str, Any]) -> str`.
- Changes `retained.decode(raw: bytes, expected_fingerprint: str, expected_sections: int) -> dict[str, Any] | None`.
- Changes `_rtc_state_load(cfg)` and `_rtc_state_save(state, cfg)` call sites in `app.py` to supply compatibility context. Task 4 then replaces the save helper with byte-oriented `_rtc_state_commit(raw, cfg)` for transactional ordering.
- Keeps the current retained frame list shape `[sections, footer, status]`.

- [ ] **Step 1: Replace retained fixtures with the versioned compatible shape**

Update `tests/test_retained.py` around these helpers:

```python
import copy
import retained


def _settings():
    return {
        "stops": [{"name": "Rosenmalm", "site_id": 1234}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 60,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
        "weather": None,
    }


def _state(cfg=None):
    cfg = cfg or _settings()
    return {
        "v": retained.STATE_VERSION,
        "render_rev": retained.RENDER_REVISION,
        "settings": retained.settings_fingerprint(cfg),
        "frame": [[{
            "name": "Rosenmalm", "hero_main": "4", "hero_unit": "min",
            "badge_line": "474", "dest": "Slussen",
            "rows": [["440", "Slussen", "12 min"]], "stale": False,
        }], ["Son 19 jul 14:32"], None],
        "last_full": 123,
        "weather": None,
        "weather_time": None,
        "weather_bucket": None,
        "last_ntp": None,
    }


def _decode(raw, cfg=None):
    cfg = cfg or _settings()
    return retained.decode(raw, retained.settings_fingerprint(cfg), len(cfg["stops"]))
```

Add tests asserting:

```python
def test_round_trip_is_strict_and_within_rtc_limit():
    state = _state()
    raw = retained.encode(state)
    assert len(raw) < retained.MAX_BYTES
    assert _decode(raw) == state


def test_old_version_renderer_and_settings_are_rejected():
    for key, value in (("v", 1), ("render_rev", 999), ("settings", "deadbeef")):
        state = _state()
        state[key] = value
        assert _decode(retained.encode(state)) is None


def test_stop_reorder_or_identity_change_invalidates_state():
    cfg = _settings()
    state = _state(cfg)
    changed = copy.deepcopy(cfg)
    changed["stops"] = [{"name": "Other", "site_id": 9999}]
    assert retained.decode(
        retained.encode(state), retained.settings_fingerprint(changed), 1,
    ) is None


def test_malformed_nested_frame_is_rejected():
    state = _state()
    state["frame"][0][0]["rows"] = [["only", "two"]]
    assert _decode(retained.encode(state)) is None


def test_oversized_raw_is_rejected_before_json_decode():
    assert _decode(b"x" * (retained.MAX_BYTES + 1)) is None
```

Retain corruption, oversize encode, and tuple/list canonicalization coverage, updated to call `_decode`.

- [ ] **Step 2: Run retained tests and verify RED**

```text
.venv\Scripts\python -m pytest tests/test_retained.py -v
```

Expected: failures for missing constants/fingerprint and the old `decode` signature.

- [ ] **Step 3: Implement strict retained validation**

Keep the existing magic/checksum envelope. Compact JSON with
`json.dumps(state, separators=(",", ":"))`. `settings_fingerprint` must hash a canonical list containing ordered `[name, site_id]` pairs, direction, departure count, and either `None` or enabled weather latitude/longitude.

Validate exact top-level state keys; frame length 3; exact section keys; section count; string fields; boolean stale; three-string rows; one/two string footer lines; status of `None`, `display.WIFI_ERROR`, `display.WEATHER_ERROR`, or a valid weather dict; and all retained timestamps as integer or `None` but never booleans. Keep `retained.py` independent of hardware and avoid importing `display`; compare status sentinel literal values locally.

Update main's next state with `v`, `render_rev`, and `settings`. Pass current compatibility values when loading and verifying saves. Old v1 RTC bytes must log incompatible and force a full refresh, never raise.

- [ ] **Step 4: Verify GREEN and compile**

```text
.venv\Scripts\python -m pytest tests/test_retained.py tests/test_display.py -v
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy src/retained.py src/app.py
.venv\Scripts\python -m mpy_cross src/retained.py
```

Expected: all commands exit 0 and encoded maximum real frame fixtures remain below 2048 bytes.

- [ ] **Step 5: Commit retained compatibility**

```text
git add src/retained.py src/app.py tests/test_retained.py
git commit -m "fix: validate retained display state"
```

---

### Task 3: Make network failures explicit and Wi-Fi recovery fresh

**Files:**
- Create: `tests/test_sl.py`
- Create: `tests/test_openmeteo.py`
- Create: `tests/test_wifi.py`
- Modify: `src/sl.py`
- Modify: `src/openmeteo.py`
- Modify: `src/wifi.py`

**Interfaces:**
- Keeps adapter public signatures, including `retries`, for awake-mode compatibility.
- Every adapter attempt requires 2xx status and expected JSON shape and closes its response.
- Extends `wifi.connect_sta(ssid, password, timeout_ms=STA_TIMEOUT_MS, wdt=None) -> bool`.
- Keeps `wifi.reconnect()` and `wifi.start_ap()` available to the existing awake/setup paths.

- [ ] **Step 1: Add failing HTTP adapter tests with fake responses**

Each adapter test installs a fake `requests` module before import and uses:

```python
class FakeResponse:
    def __init__(self, status_code, payload=None, error=None):
        self.status_code = status_code
        self.payload = payload
        self.error = error
        self.closed = False

    def json(self):
        if self.error is not None:
            raise self.error
        return self.payload

    def close(self):
        self.closed = True
```

For SL, assert a 503 JSON body raises `OSError`, missing/non-list
`departures` raises `ValueError`, invalid JSON propagates, a legitimate empty
list returns successfully, and every acquired response closes. Mirror for
Open-Meteo with required dictionary-valued `daily` and `hourly` keys. Assert
`retries=1` results in exactly one `requests.get` call.

- [ ] **Step 2: Add failing Wi-Fi recovery tests**

Use fake `network`, `time`, and watchdog objects to assert:

```python
def test_connect_resets_interface_then_connects_and_feeds_watchdog(wifi_module):
    connected = wifi_module.connect_sta("home", "secret", timeout_ms=1000, wdt=wifi_module.wdt)
    assert connected is True
    assert wifi_module.sta.events[:3] == [("active", False), ("active", True), ("connect", "home", "secret")]
    assert wifi_module.wdt.feeds > 0


def test_terminal_negative_status_returns_without_waiting_for_timeout(wifi_module):
    wifi_module.sta.statuses = [-2]
    assert wifi_module.connect_sta("home", "secret", timeout_ms=1000) is False


def test_timeout_returns_false_and_never_prints_password(wifi_module, capsys):
    wifi_module.sta.statuses = [1] * 20
    assert wifi_module.connect_sta("home", "secret", timeout_ms=200) is False
    assert "secret" not in capsys.readouterr().out
```

- [ ] **Step 3: Run adapter/Wi-Fi tests and verify RED**

```text
.venv\Scripts\python -m pytest tests/test_sl.py tests/test_openmeteo.py tests/test_wifi.py -v
```

Expected: HTTP status/shape tests and new Wi-Fi signature/order tests fail.

- [ ] **Step 4: Implement bounded explicit failures**

Inside each existing retry loop, validate `response.status_code` in `200..299`
and the required payload shape before returning. Keep `finally:
response.close()` around both validation and JSON parsing. Preserve retry
delays for awake mode; deep sleep continues calling with `retries=1`.

In `wifi.connect_sta`, deactivate the interface, sleep 100 ms, reactivate,
connect, feed optional WDT on each poll, return early for `status() < 0`, and
retain the 15-second timeout. `reconnect` delegates to this function.

- [ ] **Step 5: Verify GREEN and compile**

```text
.venv\Scripts\python -m pytest tests/test_sl.py tests/test_openmeteo.py tests/test_wifi.py -v
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy src/sl.py src/openmeteo.py src/wifi.py
.venv\Scripts\python -m mpy_cross src/sl.py
.venv\Scripts\python -m mpy_cross src/openmeteo.py
.venv\Scripts\python -m mpy_cross src/wifi.py
```

- [ ] **Step 6: Commit network hardening**

```text
git add src/sl.py src/openmeteo.py src/wifi.py tests/test_sl.py tests/test_openmeteo.py tests/test_wifi.py
git commit -m "fix: make network failures explicit"
```

---

### Task 4: Make the deep-sleep refresh a recoverable transaction

**Files:**
- Create: `tests/test_main_deep_sleep.py`
- Modify: `src/app.py`
- Modify: `src/wake_schedule.py`
- Modify: `src/weather.py`
- Modify: `tests/test_wake_schedule.py`
- Modify: `tests/test_weather.py`

**Interfaces:**
- Produces `wake_schedule.elapsed_due(now_s: float, previous_s: float | None, interval_s: int) -> bool`.
- Produces `_allocate_framebuffer() -> tuple[bytearray, Any]`.
- Produces `_rtc_state_invalidate() -> None` and `_rtc_state_commit(raw, cfg) -> None`.
- Extends `deep_sleep_cycle(..., wdt, fb, fb_buf, cold_ntp_epoch=None) -> None` to reuse boot-owned resources and carry a successful cold sync into state.
- Produces `_recover_deep_sleep(exc) -> None` for bounded 60-second recovery.
- Keeps awake-mode `display_loop()` behavior and signatures unchanged.

- [ ] **Step 1: Add failing elapsed-time tests**

In `tests/test_wake_schedule.py`:

```python
def test_elapsed_due_handles_missing_expired_and_future_values():
    assert wake_schedule.elapsed_due(1000, None, 60) is True
    assert wake_schedule.elapsed_due(1000, 940, 60) is True
    assert wake_schedule.elapsed_due(1000, 941, 60) is False
    assert wake_schedule.elapsed_due(1000, 1001, 60) is True
```

In `tests/test_weather.py` add:

```python
def test_keep_last_good_rejects_future_fetch_timestamp():
    reading = {"date": "2026-08-06", "condition": "clear", "tmin": 10, "tmax": 20, "precip": 0}
    assert weather.keep_last_good(reading, "2026-08-06", -1, 180 * 60) is False
```

- [ ] **Step 2: Add a fake-hardware main import harness**

Create `tests/test_main_deep_sleep.py`. Before importing `main`, install small
`sys.modules` fakes for `machine`, `network`, `ntptime`, and `epd7in5v2`; patch
`asyncio.run` to a recorder so module import cannot execute a real loop. The
fake RTC stores bytes, the fake WDT records feeds, fake deepsleep raises a
sentinel exception, and fake EPD records construction/init/plane/sleep events.

The tests must exercise real `main` helpers and assert:

```python
def test_changed_refresh_orders_encode_invalidate_panel_commit(app):
    app.decision_frame_changed = True
    app.run_cycle()
    assert app.events.index("rtc-empty") < app.events.index("epd-construct")
    assert app.events.index("epd-refresh") < app.events.index("rtc-commit")


def test_encode_failure_does_not_construct_epd_or_touch_rtc(app):
    app.retained.encode.side_effect = ValueError("oversize")
    with pytest.raises(ValueError, match="oversize"):
        app.run_cycle()
    assert "epd-construct" not in app.events
    assert not any(event.startswith("rtc-") for event in app.events)


def test_refresh_failure_leaves_rtc_empty_and_sleeps_panel(app):
    app.epd.fail_refresh = True
    with pytest.raises(OSError):
        app.run_cycle()
    assert app.rtc.memory() == b""
    assert "epd-sleep" in app.events


def test_unchanged_frame_commits_without_constructing_panel(app):
    app.decision_frame_changed = False
    app.run_cycle()
    assert "epd-construct" not in app.events
    assert "rtc-commit" in app.events


def test_deep_sleep_boot_starts_wifi_before_reserving_framebuffer(app):
    app.run_main_once()
    allocation = app.events.index("framebuffer")
    assert app.events.index("wdt-construct") < app.events.index("wifi-connect")
    assert app.events.index("wifi-connect") < allocation
    for later in ("rtc-read", "ntp", "epd-construct"):
        assert allocation < app.events.index(later)


def test_unexpected_deep_sleep_failure_invalidates_and_sleeps_for_one_minute(app):
    with pytest.raises(app.DeepSleepCalled):
        app.main._recover_deep_sleep(RuntimeError("boom"))
    assert app.rtc.memory() == b""
    assert app.deepsleep_ms == 60_000
```

Also assert the same `fb`/`fb_buf` identities reach old and new differential
plane writes, Wi-Fi receives the boot WDT, a future `last_full` selects full,
`machine.reset_cause()` is printed once at boot, and a partial refresh's serial
corroboration reports only the new visible frame rather than the reconstructed
old plane.

- [ ] **Step 3: Run focused tests and verify RED**

```text
.venv\Scripts\python -m pytest tests/test_main_deep_sleep.py tests/test_wake_schedule.py tests/test_weather.py -v
```

Expected: missing helper/signature failures and transaction-order assertions fail against current main.

- [ ] **Step 4: Implement the smallest deep-sleep-only orchestration changes**

Add `elapsed_due`; make `weather.keep_last_good` require `0 <= age_s <=
max_age_s`. In deep-sleep mode only:

1. validate config;
2. start one WDT;
3. call `wifi.connect_sta(..., wdt=wdt)` so STA reserves RX buffers first;
4. allocate exactly one `fb_buf`/`fb`;
5. load strict RTC state;
6. cold-sync NTP under WDT;
7. recalculate the request boundary;
8. call `deep_sleep_cycle` with the owned WDT/framebuffer and successful cold
   NTP epoch, if any.

Remove EPD construction and framebuffer allocation from the top of
`deep_sleep_cycle`. When a frame changes, build and encode `next_state`, first
strict-decode the candidate in the current settings/section context, clear and
verify RTC, construct EPD inside the refresh closure/block, refresh under the
existing `_safe_sleep`, then commit exact verified bytes. An invalid candidate
touches neither RTC nor EPD. On unchanged content, encode, preflight, and commit
without invalidation or panel construction.

Use `elapsed_due` for weather, NTP, and full-refresh checks. Print a stable
numeric `main: reset cause = N` line. Catch unexpected exceptions around only
the deep-sleep branch and call `_recover_deep_sleep`; keep `KeyboardInterrupt`,
configuration error, and awake-mode behavior intact.

Integration-closeout amendments from final review:

- Strict-decode every encoded candidate before RTC invalidation or EPD work.
- Canonicalize fresh secondary departure rows as lists before frame equality.
- Validate weather usability on every wake and force a fetch on date mismatch,
  even when its elapsed pull interval is not due. Run a due daily NTP resync
  before weather/footer local-date decisions so a midnight correction cannot
  validate yesterday's forecast and then render today's footer.
- Reject blank stop names and cap names at 48 characters so the committed
  42-character example remains valid; exercise a two-stop, three-departure,
  non-BMP-name frame with all semantic fields at their test maxima under the
  2048-byte budget (1975 bytes, 73-byte headroom).
- Recovery disables STA, invalidates RTC, sleeps for 60 seconds, and resets if
  deep sleep returns. Missing credentials remain an ordinary committed
  Wi-Fi-error wake. The ordinary next-wake `deepsleep()` call has the same hard
  reset fallback if it unexpectedly returns.
- Carry a successful cold-boot NTP epoch into the first cycle so it is retained
  without a duplicate NTP request.

- [ ] **Step 5: Verify GREEN, full suite, typing, and compilation**

```text
.venv\Scripts\python -m pytest tests/test_main_deep_sleep.py tests/test_retained.py tests/test_wake_schedule.py tests/test_weather.py -v
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/app.py
.venv\Scripts\python -m mpy_cross src/wake_schedule.py
.venv\Scripts\python -m mpy_cross src/weather.py
```

Expected: all commands exit 0. Runtime `app.py` compiles for deployment;
source `main.py` remains only the tiny activation shim.

- [ ] **Step 6: Commit the transaction and recovery path**

```text
git add src/app.py src/wake_schedule.py src/weather.py tests/test_main_deep_sleep.py tests/test_wake_schedule.py tests/test_weather.py
git commit -m "fix: make deep-sleep wakes recover safely"
```

---

### Task 5: Activate deployments last and record settled reliability rules

**Files:**
- Create: `tests/test_deploy.py`
- Modify: `deploy.bat`
- Modify: `AGENTS.md`
- Modify: `README.md`

**Interfaces:**
- Keeps the current wildcard compile/copy inventory and legacy device files.
- Changes only deployment ordering: support bytecode/config/fonts first,
  `main.py` last, reset after success.
- Records the finalized reliability behavior in repository guidance and removes
  answered items from `AGENTS.md` open questions if present.

- [ ] **Step 1: Write a failing deployment-order test**

Create `tests/test_deploy.py`:

```python
from pathlib import Path


def test_main_is_the_last_firmware_file_copied_before_reset():
    text = Path("deploy.bat").read_text(encoding="utf-8")
    main_copy = text.index('fs cp "%SRCDIR%\\main.py" ":main.py"')
    module_copy = text.index('fs cp "%%F" ":%%~nxF"')
    lib_copy = text.index('fs cp "%%F" ":lib/%%~nxF"')
    font_copy = text.index('fs cp "%%F" ":fonts/%%~nxF"')
    reset = text.index(" reset", main_copy)
    assert module_copy < main_copy
    assert lib_copy < main_copy
    assert font_copy < main_copy
    assert main_copy < reset
```

- [ ] **Step 2: Run the deployment test and verify RED**

```text
.venv\Scripts\python -m pytest tests/test_deploy.py -v
```

Expected: FAIL because `main.py` currently participates in the first wildcard copy loop.

- [ ] **Step 3: Reorder deployment without broad cleanup**

Exclude `main.py` from the first top-level loop, copy modules/JSON, vendored
libraries, and fonts exactly as today, then execute:

```bat
echo   cp main.py
%MP% %CONN% fs cp "%SRCDIR%\main.py" ":main.py"
if errorlevel 1 goto :fail
```

immediately before reset. Do not delete server/lib files or introduce the cleanup branch's explicit module manifest.

- [ ] **Step 4: Update reliability documentation**

In `AGENTS.md`, record strict RTC compatibility, candidate validation plus
invalidate-refresh-commit ordering, WDT -> Wi-Fi -> framebuffer allocation,
WDT-covered Wi-Fi/NTP, fresh per-wake
Wi-Fi reset, bounded fatal deep-sleep recovery, future timestamp handling, and
`main.py`-last deployment. Preserve all hardware facts and awake-mode notes.

In `README.md`, add a concise production behavior paragraph and the hardware
acceptance command sequence. Do not claim hardware success yet; label it as
required verification.

- [ ] **Step 5: Verify documentation/deployment changes**

```text
.venv\Scripts\python -m pytest tests/test_deploy.py -v
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy src
git diff --check
```

- [ ] **Step 6: Commit deployment and guidance**

```text
git add deploy.bat AGENTS.md README.md tests/test_deploy.py
git commit -m "chore: harden firmware activation order"
```

---

### Task 5A: Precompile the runtime to preserve the clean boot heap

**Files:**
- Move unchanged runtime: `src/main.py` -> `src/app.py`
- Create: `src/main.py`
- Modify: `tests/test_main_deep_sleep.py`
- Modify: `tests/test_deploy.py`
- Modify: `pyproject.toml`
- Modify: `deploy.bat`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-06-deep-sleep-reliability-hardening-design.md`

**Interfaces:**
- `src/main.py` is a tiny source boot shim containing `import app`.
- `src/app.py` contains the existing runtime unchanged and is compiled by the
  existing top-level `*.py` wildcard into `app.mpy`.
- Support bytecode, including `app.mpy`, is copied before source `main.py`;
  `main.mpy` is neither compiled nor shipped.

- [ ] **Step 1: Add packaging and real-runtime RED tests**

Update the fake-hardware harness to import `src/app.py`. Add source/deployment
assertions that `main.py` is tiny and imports `app`, `app.py` participates in
the compile wildcard, support `.mpy` copies precede `:main.py`, and the existing
`main.mpy` exclusions remain in both compile and copy paths.

- [ ] **Step 2: Run the focused tests and verify RED**

```text
.venv\Scripts\python -m pytest tests/test_main_deep_sleep.py tests/test_deploy.py -q
```

Expected: fail because `src/app.py` does not exist and source `main.py` is the
full runtime rather than a tiny `import app` shim.

- [ ] **Step 3: Move runtime bytes unchanged and add the shim**

Move `src/main.py` to `src/app.py` without editing its contents. Create a tiny
`src/main.py` whose concise comment records that importing the precompiled
runtime avoids fragmenting the heap before Wi-Fi plus the 48 KB framebuffer.
Its only executable statement is:

```python
import app
```

- [ ] **Step 4: Update typing, deployment comments, and architecture docs**

Keep mypy's `files = ["src"]` coverage and document that it includes both the
shim and runtime. Update deploy comments (not its proven wildcard/activation
mechanics), AGENTS, README, design, and this plan so all runtime references use
`app.py`, while `main.py` remains source and activation-last.

- [ ] **Step 5: Verify host behavior and packaging**

```text
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m mpy_cross src/app.py
.venv\Scripts\python -m mpy_cross src/main.py
git diff --check
```

Expected: all pass; mypy checks 16 first-party modules. Do not touch COM3 in
this task.

- [ ] **Step 6: Commit**

```text
git commit -m "fix: precompile firmware runtime for heap safety"
```

### Task 6: Final host audit and COM3 hardware acceptance

**Files:**
- Modify only if verification exposes a regression; any fix requires a new failing test and its own commit.

**Interfaces:**
- Consumes the completed reliability branch.
- Produces evidence for merge readiness; it does not merge.

- [ ] **Step 1: Run fresh complete host verification**

```text
.venv\Scripts\python -m pytest -v
.venv\Scripts\python -m mypy src
```

Compile every first-party deployable source plus the source entry point:

```powershell
$modules = Get-ChildItem src -File -Filter *.py
foreach ($module in $modules) {
    .venv\Scripts\python -m mpy_cross $module.FullName
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Run `git diff --check` and inspect `git status --short`.

- [ ] **Step 2: Review the complete branch diff against the spec**

```text
git diff --stat main...HEAD
git diff --check main...HEAD
git log --oneline main..HEAD
```

Check every design requirement, confirm no panel command sequence changed,
confirm awake/setup files remain, and resolve every Critical/Important review
finding with a failing regression test.

- [ ] **Step 3: Inspect COM3 without exposing secrets**

```text
.venv\Scripts\python -m mpremote connect list
.venv\Scripts\python -m mpremote connect COM3 fs ls
```

Record filenames/sizes only. Do not print `/config.json` or private settings.

- [ ] **Step 4: Deploy and observe five normal wakes**

```text
deploy.bat COM3
.venv\Scripts\python -m mpremote connect COM3 repl
```

Capture reset cause, retained-state result, connection result, request lateness,
refresh mode, final visible-frame summary, retained commit, and next sleep for
at least five minute boundaries. Ask the owner to confirm each visible update
and that full/partial behavior looks normal.

- [ ] **Step 5: Verify retained incompatibility recovery**

After preserving normal logs, clear only RTC user memory with a one-off
`mpremote exec`, reset, and observe one full refresh. Observe the following
changed wake and confirm it returns to partial. Confirm the panel sleeps after
both.

- [ ] **Step 6: Verify Wi-Fi outage and recovery with owner approval**

Use an owner-approved router outage or temporary device credential test. Never
overwrite the only credential copy without first preserving it securely. Verify
that the screen shows `Wi-Fi unavailable` plus stale stop badges, schedules the
next wake, and automatically returns to fresh departures after Wi-Fi comes
back.

- [ ] **Step 7: Re-run host verification after hardware findings**

```text
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m mypy src
git status --short --branch
```

Report exact pass counts, compile results, observed wake sequence, remaining
risks, and owner visual confirmation. Do not merge; hand back the verified
branch for an explicit merge decision.
