"""Deep-sleep orchestration tests with deterministic fake hardware.

The real ``app.py`` runtime is imported for every test while only its hardware
boundaries are replaced. Import-time ``asyncio.run`` is blocked, then the real
coroutines/helpers are exercised explicitly. ``monkeypatch`` restores every
``sys.modules`` entry so fake ESP32 modules cannot leak into the host suite.
"""
import asyncio
import importlib.util
import inspect
from pathlib import Path
import sys
import types

import pytest


SRC = Path(__file__).parents[1] / "src"
_MISSING = object()


def _valid_cfg(weather=None):
    cfg = {
        "stops": [{"name": "Rosenmalm", "site_id": 1234}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 60,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
    }
    if weather is not None:
        cfg["weather"] = weather
    return cfg


def _section(stale=True):
    return {
        "name": "Rosenmalm", "hero_main": None, "hero_unit": None,
        "badge_line": None, "dest": "No departures", "rows": [],
        "stale": stale,
    }


class DeepSleepCalled(Exception):
    pass


@pytest.fixture
def app(monkeypatch):
    events = []

    class FakeRTC:
        def __init__(self):
            self.raw = b"seed"
            self.ignore_clear = False
            self.read_override = None

        def memory(self, value=_MISSING):
            if value is _MISSING:
                events.append("rtc-read")
                return self.raw if self.read_override is None else self.read_override
            if value == b"":
                events.append("rtc-empty")
                if not self.ignore_clear:
                    self.raw = b""
            else:
                events.append("rtc-commit")
                self.raw = value

    class FakeWDT:
        def __init__(self, timeout=None):
            self.timeout = timeout
            self.feeds = 0
            events.append("wdt-construct")

        def feed(self):
            self.feeds += 1
            events.append("wdt-feed")

    rtc = FakeRTC()
    machine = types.ModuleType("machine")
    machine.RTC = lambda: rtc
    machine.wdts = []

    def make_wdt(timeout=None):
        wdt = FakeWDT(timeout)
        machine.wdts.append(wdt)
        return wdt

    machine.WDT = make_wdt
    machine.deepsleep_calls = []
    machine.raise_on_deepsleep = False

    def deepsleep(delay_ms):
        machine.deepsleep_calls.append(delay_ms)
        events.append(("deepsleep", delay_ms))
        if machine.raise_on_deepsleep:
            raise DeepSleepCalled()

    machine.deepsleep = deepsleep
    machine.reset_calls = 0

    def reset():
        machine.reset_calls += 1
        events.append("reset")

    machine.reset = reset
    machine.reset_cause_calls = 0

    def reset_cause():
        machine.reset_cause_calls += 1
        events.append("reset-cause")
        return 5

    machine.reset_cause = reset_cause

    network = types.ModuleType("network")
    network.STA_IF = 0

    class FakeWLAN:
        def active(self, value=None):
            if value is False:
                events.append("wifi-off")
            return True

        def ifconfig(self):
            return ("192.0.2.1", "255.255.255.0", "192.0.2.1", "192.0.2.1")

    network.WLAN = lambda _interface: FakeWLAN()

    ntptime = types.ModuleType("ntptime")

    def settime():
        events.append("ntp")

    ntptime.settime = settime

    framebuf = types.ModuleType("framebuf")
    framebuf.MONO_HLSB = 0
    framebuf.sizes = []

    class FakeFrameBuffer:
        def __init__(self, buf, width, height, fmt):
            self.buf = buf
            self.width = width
            self.height = height
            framebuf.sizes.append(len(buf))
            events.append("framebuffer")

    framebuf.FrameBuffer = FakeFrameBuffer

    epd_module = types.ModuleType("epd7in5v2")
    epd_module.fail_refresh = False
    epd_module.instances = []

    class FakeEPD:
        def __init__(self):
            events.append("epd-construct")
            epd_module.instances.append(self)

        def init(self):
            events.append("epd-init-full")

        def display(self, buf):
            events.append(("epd-full-buffer", id(buf)))
            events.append("epd-refresh")
            if epd_module.fail_refresh:
                raise OSError("refresh failed")

        def init_part(self):
            events.append("epd-init-part")

        def partial_begin(self):
            events.append("epd-partial-begin")

        def partial_old(self, buf):
            events.append(("epd-old-buffer", id(buf)))

        def partial_new(self, buf):
            events.append(("epd-new-buffer", id(buf)))
            events.append("epd-refresh")
            if epd_module.fail_refresh:
                raise OSError("refresh failed")

        def sleep(self):
            events.append("epd-sleep")

    epd_module.EPD7in5V2 = FakeEPD

    requests = types.ModuleType("requests")

    def unexpected_http(*_args, **_kwargs):
        raise AssertionError("deep-sleep harness attempted real HTTP")

    requests.get = unexpected_http

    wifi_module = types.ModuleType("wifi")
    wifi_module.connect_sta = lambda *_args, **_kwargs: False
    wifi_module.reconnect = lambda *_args, **_kwargs: False
    sl_module = types.ModuleType("sl")
    sl_module.fetch_departures = unexpected_http
    openmeteo_module = types.ModuleType("openmeteo")
    openmeteo_module.fetch_today = unexpected_http

    fake_modules = {
        "machine": machine,
        "network": network,
        "ntptime": ntptime,
        "framebuf": framebuf,
        "epd7in5v2": epd_module,
        "requests": requests,
        "wifi": wifi_module,
        "sl": sl_module,
        "openmeteo": openmeteo_module,
    }
    for name, fake_module in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, fake_module)
    import_run_calls = []

    def block_import_run(coro):
        import_run_calls.append(coro)
        coro.close()

    real_asyncio_run = asyncio.run
    monkeypatch.setattr(asyncio, "run", block_import_run)
    module_name = "_task4_app_%d" % id(events)
    spec = importlib.util.spec_from_file_location(module_name, SRC / "app.py")
    assert spec is not None and spec.loader is not None
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    monkeypatch.setattr(asyncio, "run", real_asyncio_run)
    assert len(import_run_calls) == 1

    now = 1_000
    main.time = types.SimpleNamespace(
        time=lambda: now,
        ticks_ms=lambda: 10,
        ticks_diff=lambda current, previous: current - previous,
        gmtime=lambda: (2026, 8, 6, 10, 0, 0, 0, 0),
        sleep=lambda _seconds: None,
    )

    async def no_wait(_wdt, _target):
        return None

    monkeypatch.setattr(main, "_wait_until_epoch", no_wait)
    monkeypatch.setattr(main, "_local_now_strings", lambda: ("Tor 6 aug", "12:00"))
    monkeypatch.setattr(main, "_local_today_iso", lambda: "2026-08-06")
    monkeypatch.setattr(main, "_fetch_all_stops", lambda cfg, retries=1, wdt=None: [[]])

    visible_logs = []
    draw_calls = []

    def draw_home(fb, sections, footer, status=None, log=True):
        events.append("render")
        frame = [sections, footer, status]
        draw_calls.append((fb, frame, log))
        if log:
            visible_logs.append(footer[0])
            print("display: visible " + footer[0])
        return 0, 1

    monkeypatch.setattr(main.display, "draw_home", draw_home)

    real_encode = main.retained.encode
    encoded = []

    def record_encode(state):
        events.append("encode")
        raw = real_encode(state)
        encoded.append(raw)
        return raw

    monkeypatch.setattr(main.retained, "encode", record_encode)

    cfg = _valid_cfg()
    state_unchanged = {
        "v": main.retained.STATE_VERSION,
        "render_rev": main.retained.RENDER_REVISION,
        "settings": main.retained.settings_fingerprint(cfg),
        "frame": [[_section()], ["Tor 6 aug 12:00"], "wifi_error"],
        "last_full": 900,
        "weather": None,
        "weather_time": None,
        "weather_bucket": None,
        "last_ntp": None,
    }

    namespace = types.SimpleNamespace(
        main=main, machine=machine, rtc=rtc, epd=epd_module,
        framebuf=framebuf, events=events, draw_calls=draw_calls,
        visible_logs=visible_logs, encoded=encoded,
        DeepSleepCalled=DeepSleepCalled, cfg=cfg,
        state_unchanged=state_unchanged, wifi_wdt=None, fetch_wdt=None,
        fail_wifi_if_framebuffer=False, patch=monkeypatch,
    )

    def resources():
        fb_buf = bytearray(48_000)
        fb = FakeFrameBuffer(fb_buf, 800, 480, framebuf.MONO_HLSB)
        wdt = FakeWDT(main.WDT_TIMEOUT_MS)
        events.clear()
        framebuf.sizes.clear()
        return wdt, fb, fb_buf

    def run_cycle(
        changed=True, state=None, connected=False, cfg=None,
        cold_ntp_epoch=None,
    ):
        cfg = cfg or namespace.cfg
        state = dict(state or namespace.state_unchanged)
        state["frame"] = [
            list(state["frame"][0]), list(state["frame"][1]), state["frame"][2],
        ]
        if changed:
            state["frame"][1] = ["Old footer"]
        rtc.raw = b"seed"
        events.clear()
        draw_calls.clear()
        visible_logs.clear()
        encoded.clear()
        wdt, fb, fb_buf = resources()
        args = (cfg, {"ssid": "home", "password": "secret"}, connected,
                state, now, 0)
        if "wdt" in inspect.signature(main.deep_sleep_cycle).parameters:
            args += (wdt, fb, fb_buf)
        if "cold_ntp_epoch" in inspect.signature(main.deep_sleep_cycle).parameters:
            args += (cold_ntp_epoch,)
        real_asyncio_run(main.deep_sleep_cycle(*args))
        return wdt, fb, fb_buf

    def run_main_once(cfg=None, wifi_cfg=_MISSING):
        cfg = cfg or namespace.cfg
        if wifi_cfg is _MISSING:
            wifi_cfg = {"ssid": "home", "password": "secret"}
        rtc.raw = b""
        events.clear()
        framebuf.sizes.clear()
        encoded.clear()
        monkeypatch.setattr(main.settings, "load", lambda: cfg)
        monkeypatch.setattr(
            main.config, "load",
            lambda: {} if wifi_cfg is None else {"wifi": wifi_cfg},
        )

        def connect_sta(_ssid, _password, timeout_ms=15_000, wdt=None):
            events.append("wifi-connect")
            namespace.wifi_wdt = wdt
            if namespace.fail_wifi_if_framebuffer and "framebuffer" in events:
                raise OSError("WiFi Out of Memory")
            return True

        monkeypatch.setattr(main.wifi, "connect_sta", connect_sta)

        def fetch(cfg, retries=1, wdt=None):
            events.append("fetch")
            namespace.fetch_wdt = wdt
            return [[]]

        monkeypatch.setattr(main, "_fetch_all_stops", fetch)
        real_asyncio_run(main.main())

    namespace.resources = resources
    namespace.run_cycle = run_cycle
    namespace.run_main_once = run_main_once
    yield namespace


def test_changed_refresh_orders_encode_invalidate_panel_commit(app):
    app.run_cycle(changed=True)
    assert app.events.index("encode") < app.events.index("rtc-empty")
    assert app.events.index("rtc-empty") < app.events.index("epd-construct")
    assert app.events.index("epd-refresh") < app.events.index("rtc-commit")
    assert app.rtc.raw == app.encoded[-1]


def test_encode_failure_does_not_construct_epd_or_touch_rtc(app, monkeypatch):
    def fail_encode(_state):
        app.events.append("encode")
        raise ValueError("oversize")

    monkeypatch.setattr(app.main.retained, "encode", fail_encode)
    with pytest.raises(ValueError, match="oversize"):
        app.run_cycle(changed=True)
    assert "epd-construct" not in app.events
    assert not any(
        isinstance(event, str) and event.startswith("rtc-")
        for event in app.events
    )


@pytest.mark.parametrize("changed", [True, False])
def test_invalid_encoded_candidate_does_not_construct_epd_or_touch_rtc(
    app, monkeypatch, changed,
):
    decode_calls = []

    def reject_candidate(raw, fingerprint, section_count):
        decode_calls.append((raw, fingerprint, section_count))
        return None

    monkeypatch.setattr(app.main.retained, "decode", reject_candidate)
    with pytest.raises(ValueError, match="strict validation"):
        app.run_cycle(changed=changed)
    assert decode_calls == [(
        app.encoded[-1],
        app.main.retained.settings_fingerprint(app.cfg),
        len(app.cfg["stops"]),
    )]
    assert "epd-construct" not in app.events
    assert not any(
        isinstance(event, str) and event.startswith("rtc-")
        for event in app.events
    )


def test_refresh_failure_leaves_rtc_empty_and_sleeps_panel(app):
    app.epd.fail_refresh = True
    with pytest.raises(OSError, match="refresh failed"):
        app.run_cycle(changed=True)
    assert app.rtc.raw == b""
    assert "epd-sleep" in app.events
    assert "rtc-commit" not in app.events


def test_unchanged_frame_commits_without_constructing_panel(app):
    app.run_cycle(changed=False)
    assert "epd-construct" not in app.events
    assert app.events.index("encode") < app.events.index("rtc-commit")
    assert "rtc-empty" not in app.events


def test_secondary_departure_row_is_canonical_and_unchanged_skips_panel(
    app, monkeypatch,
):
    deps = [
        {"line": "474", "destination": "Slussen", "display": "4 min"},
        {"line": "440", "destination": "Nacka", "display": "12 min"},
    ]
    section = app.main.display.stop_section("Rosenmalm", deps, stale=False)
    section["rows"] = [list(row) for row in section["rows"]]
    state = dict(app.state_unchanged)
    state.update(
        frame=[[section], ["Tor 6 aug 12:00"], None],
        last_ntp=1_000,
    )
    monkeypatch.setattr(
        app.main, "_fetch_all_stops",
        lambda cfg, retries=1, wdt=None: [deps],
    )
    app.run_cycle(changed=False, state=state, connected=True)
    assert "epd-construct" not in app.events


def test_deep_sleep_boot_starts_wifi_before_reserving_framebuffer(app):
    # Hardware regression: ESP32 STA initialization needs its RX buffers before
    # the resident 48 KB framebuffer takes the largest clean heap region.
    app.fail_wifi_if_framebuffer = True
    app.run_main_once()
    allocation = app.events.index("framebuffer")
    watchdog = app.events.index("wdt-construct")
    wifi_connect = app.events.index("wifi-connect")
    assert watchdog < wifi_connect < allocation
    for later in ("rtc-read", "ntp", "fetch", "render", "epd-construct"):
        assert allocation < app.events.index(later)
    assert app.framebuf.sizes == [48_000]
    assert len(app.machine.wdts) == 1
    assert app.wifi_wdt is app.machine.wdts[-1]
    assert app.fetch_wdt is app.wifi_wdt
    assert 60_000 not in app.machine.deepsleep_calls


def test_rtc_invalidation_verifies_the_clear(app):
    app.rtc.ignore_clear = True
    with pytest.raises(OSError, match="invalidate"):
        app.main._rtc_state_invalidate()


def test_rtc_commit_requires_exact_strictly_decodable_readback(app):
    raw = app.main.retained.encode(app.state_unchanged)
    app.rtc.read_override = raw + b"corrupt"
    with pytest.raises(OSError, match="readback mismatch"):
        app.main._rtc_state_commit(raw, app.cfg)

    app.rtc.read_override = None
    with pytest.raises(OSError, match="readback mismatch"):
        app.main._rtc_state_commit(b"not retained state", app.cfg)


def test_partial_refresh_reuses_buffer_and_logs_only_new_visible_frame(app):
    _wdt, fb, fb_buf = app.resources()
    epd = app.epd.EPD7in5V2()
    old = [[_section()], ["OLD"], None]
    new = [[_section()], ["NEW"], None]
    app.events.clear()
    app.main._draw_and_refresh(epd, fb, fb_buf, new, old, full=False)
    assert ("epd-old-buffer", id(fb_buf)) in app.events
    assert ("epd-new-buffer", id(fb_buf)) in app.events
    assert [call[0] for call in app.draw_calls] == [fb, fb]
    assert app.visible_logs == ["NEW"]


def test_future_last_full_timestamp_forces_full_refresh(app):
    state = dict(app.state_unchanged)
    state["last_full"] = 1_001
    app.run_cycle(changed=True, state=state)
    assert "epd-init-full" in app.events
    assert "epd-init-part" not in app.events


def test_future_weather_and_ntp_timestamps_cannot_suppress_work(app, monkeypatch):
    weather_cfg = {
        "enabled": True, "latitude": 59.33, "longitude": 18.06,
        "pull_interval_min": 30, "max_age_min": 180,
    }
    cfg = _valid_cfg(weather_cfg)
    reading = {
        "date": "2026-08-06", "condition": "clear",
        "tmin": 10, "tmax": 20, "precip": 0,
    }
    state = dict(app.state_unchanged)
    state.update(
        settings=app.main.retained.settings_fingerprint(cfg),
        weather=reading,
        weather_time=1_001,
        weather_bucket=0,
        last_ntp=1_001,
    )
    weather_fetches = []

    def fetch_today(_latitude, _longitude, retries=1):
        weather_fetches.append(True)
        return {
            "daily": {
                "time": ["2026-08-06"],
                "temperature_2m_max": [20], "temperature_2m_min": [10],
            },
            "hourly": {
                "time": ["2026-08-06T12:00"], "weather_code": [0],
                "precipitation_probability": [0],
            },
        }

    monkeypatch.setattr(app.main.openmeteo, "fetch_today", fetch_today)
    app.run_cycle(changed=True, state=state, connected=True, cfg=cfg)
    assert weather_fetches == [True]
    assert "ntp" in app.events


def test_due_ntp_resync_runs_before_request_boundary(app, monkeypatch):
    state = dict(app.state_unchanged)
    state["last_ntp"] = None

    async def record_boundary(_wdt, _target):
        app.events.append("boundary-wait")

    monkeypatch.setattr(app.main, "_wait_until_epoch", record_boundary)
    app.run_cycle(changed=True, state=state, connected=True)

    assert app.events.index("ntp") < app.events.index("boundary-wait")


def test_deep_sleep_ntp_resync_is_due_after_five_minutes(app):
    state = dict(app.state_unchanged)
    state["last_ntp"] = 701
    app.run_cycle(changed=True, state=state, connected=True)
    assert "ntp" not in app.events

    state["last_ntp"] = 700
    app.run_cycle(changed=True, state=state, connected=True)
    assert "ntp" in app.events


def test_main_prints_numeric_reset_cause_once_per_boot(app, capsys):
    app.run_main_once()
    output = capsys.readouterr().out
    assert output.count("main: reset cause = 5") == 1
    assert app.machine.reset_cause_calls == 1


def test_cold_boot_recalculates_request_boundary_after_ntp_attempt(app, monkeypatch):
    clock = [1_001]
    boundary_inputs = []
    original_boundary = app.main.wake_schedule.request_boundary
    app.main.time.time = lambda: clock[0]

    def settime():
        app.events.append("ntp")
        clock[0] = 1_020

    def request_boundary(now_s, interval_s=60):
        boundary_inputs.append(now_s)
        return original_boundary(now_s, interval_s)

    monkeypatch.setattr(app.main.ntptime, "settime", settime)
    monkeypatch.setattr(app.main.wake_schedule, "request_boundary", request_boundary)
    app.run_main_once()
    assert boundary_inputs == [1_001, 1_020]


def test_retained_wake_keeps_boundary_chosen_before_slow_wifi(app, monkeypatch):
    """A :57 wake must still target :00 if Wi-Fi finishes after :00."""
    captured_request_epochs = []

    app.main.time.time = lambda: (
        1_077 if "wifi-connect" not in app.events else 1_081
    )
    monkeypatch.setattr(
        app.main, "_rtc_state_load", lambda _cfg: app.state_unchanged,
    )

    async def capture_cycle(
        _cfg, _wifi_cfg, _connected, _state, request_epoch, *_resources,
    ):
        captured_request_epochs.append(request_epoch)

    monkeypatch.setattr(app.main, "deep_sleep_cycle", capture_cycle)

    app.run_main_once()

    assert captured_request_epochs == [1_080]


def test_successful_cold_boot_ntp_is_not_repeated_and_is_retained(app):
    app.run_main_once()
    assert app.events.count("ntp") == 1
    decoded = app.main.retained.decode(
        app.rtc.raw, app.main.retained.settings_fingerprint(app.cfg), 1,
    )
    assert decoded is not None
    assert decoded["last_ntp"] == 1_000


def test_unexpected_deep_sleep_failure_invalidates_and_sleeps_for_one_minute(
    app, monkeypatch, capsys,
):
    app.machine.raise_on_deepsleep = True
    monkeypatch.setattr(
        app.main.sys, "print_exception", lambda exc: print(repr(exc)), raising=False,
    )
    with pytest.raises(DeepSleepCalled):
        app.main._recover_deep_sleep(RuntimeError("boom"))
    assert app.rtc.raw == b""
    assert app.machine.deepsleep_calls[-1] == 60_000
    assert app.machine.reset_calls == 0
    assert app.events.index("wifi-off") < app.events.index("rtc-empty")
    assert app.events.index("rtc-empty") < app.events.index(("deepsleep", 60_000))
    assert "boom" in capsys.readouterr().out


def test_recovery_resets_if_deep_sleep_unexpectedly_returns(app, capsys):
    app.main._recover_deep_sleep(RuntimeError("boom"))
    assert app.rtc.raw == b""
    assert app.machine.deepsleep_calls[-1] == 60_000
    assert app.machine.reset_calls == 1
    assert app.events.index("wifi-off") < app.events.index("rtc-empty")
    assert app.events.index("rtc-empty") < app.events.index(("deepsleep", 60_000))
    assert app.events.index(("deepsleep", 60_000)) < app.events.index("reset")
    assert "boom" in capsys.readouterr().out


def test_normal_sleep_resets_if_deep_sleep_unexpectedly_returns(app):
    app.run_cycle(changed=False)
    delay_ms = app.machine.deepsleep_calls[-1]
    assert delay_ms != 60_000
    assert app.machine.reset_calls == 1
    assert app.events.index(("deepsleep", delay_ms)) < app.events.index("reset")


def test_main_routes_unexpected_deep_sleep_failure_to_recovery(app, monkeypatch):
    app.rtc.raw = b"seed"
    monkeypatch.setattr(app.main.settings, "load", lambda: app.cfg)
    monkeypatch.setattr(app.main.config, "load", lambda: {})

    async def fail_cycle(*_args):
        raise RuntimeError("cycle failed")

    monkeypatch.setattr(app.main, "deep_sleep_cycle", fail_cycle)
    app.machine.raise_on_deepsleep = True
    monkeypatch.setattr(
        app.main.sys, "print_exception", lambda exc: None, raising=False,
    )
    with pytest.raises(DeepSleepCalled):
        asyncio.run(app.main.main())
    assert app.rtc.raw == b""
    assert app.machine.deepsleep_calls[-1] == 60_000


def test_expected_weather_failure_commits_error_frame_and_uses_next_wake(
    app, monkeypatch,
):
    cfg = _valid_cfg({
        "enabled": True, "latitude": 59.33, "longitude": 18.06,
        "pull_interval_min": 30, "max_age_min": 180,
    })

    def fail_weather(*_args, **_kwargs):
        raise OSError("weather offline")

    monkeypatch.setattr(app.main.openmeteo, "fetch_today", fail_weather)
    monkeypatch.setattr(
        app.main, "_fetch_all_stops",
        lambda cfg, retries=1, wdt=None: [None],
    )
    app.run_cycle(changed=True, state=None, connected=True, cfg=cfg)
    assert "rtc-commit" in app.events
    decoded = app.main.retained.decode(
        app.rtc.raw, app.main.retained.settings_fingerprint(cfg), 1,
    )
    assert decoded is not None
    assert decoded["frame"][0][0]["stale"] is True
    assert decoded["frame"][2] == app.main.display.WEATHER_ERROR
    assert app.machine.deepsleep_calls[-1] != 60_000


def test_retained_invalid_weather_payload_uses_normal_weather_error_policy(
    app, monkeypatch,
):
    cfg = _valid_cfg({
        "enabled": True, "latitude": 59.33, "longitude": 18.06,
        "pull_interval_min": 30, "max_age_min": 180,
    })
    monkeypatch.setattr(app.main.openmeteo, "fetch_today", lambda *_args, **_kwargs: {
        "daily": {
            "time": [7],
            "temperature_2m_max": [20], "temperature_2m_min": [10],
        },
        "hourly": {
            "time": ["2026-08-06T12:00"], "weather_code": [0],
            "precipitation_probability": [0],
        },
    })
    app.run_cycle(changed=True, state=None, connected=True, cfg=cfg)
    decoded = app.main.retained.decode(
        app.rtc.raw, app.main.retained.settings_fingerprint(cfg), 1,
    )
    assert decoded is not None
    assert decoded["weather"] is None
    assert decoded["frame"][2] == app.main.display.WEATHER_ERROR
    assert app.machine.deepsleep_calls[-1] != 60_000


def test_prior_day_weather_fetches_immediately_even_inside_interval(
    app, monkeypatch,
):
    cfg = _valid_cfg({
        "enabled": True, "latitude": 59.33, "longitude": 18.06,
        "pull_interval_min": 30, "max_age_min": 180,
    })
    old_reading = {
        "date": "2026-08-05", "condition": "clear",
        "tmin": 10, "tmax": 20, "precip": 0,
    }
    state = dict(app.state_unchanged)
    state.update(
        settings=app.main.retained.settings_fingerprint(cfg),
        frame=[[_section(stale=False)], ["Tor 6 aug 12:00"], old_reading],
        weather=old_reading, weather_time=999, weather_bucket=0,
        last_ntp=1_000,
    )
    fetches = []

    def fetch_today(*_args, **_kwargs):
        fetches.append(True)
        return {
            "daily": {
                "time": ["2026-08-06"],
                "temperature_2m_max": [21], "temperature_2m_min": [11],
            },
            "hourly": {
                "time": ["2026-08-06T12:00"], "weather_code": [0],
                "precipitation_probability": [0],
            },
        }

    monkeypatch.setattr(app.main.openmeteo, "fetch_today", fetch_today)
    app.run_cycle(changed=False, state=state, connected=True, cfg=cfg)
    assert fetches == [True]
    decoded = app.main.retained.decode(
        app.rtc.raw, app.main.retained.settings_fingerprint(cfg), 1,
    )
    assert decoded is not None
    assert decoded["weather"]["date"] == "2026-08-06"


def test_due_ntp_crossing_midnight_rechecks_weather_before_footer(
    app, monkeypatch,
):
    cfg = _valid_cfg({
        "enabled": True, "latitude": 59.33, "longitude": 18.06,
        "pull_interval_min": 30, "max_age_min": 180,
    })
    clock = {"date": "2026-08-05"}
    old_reading = {
        "date": "2026-08-05", "condition": "clear",
        "tmin": 10, "tmax": 20, "precip": 0,
    }
    state = dict(app.state_unchanged)
    state.update(
        settings=app.main.retained.settings_fingerprint(cfg),
        frame=[[app.main.display.stop_section(
            "Rosenmalm", [], stale=False,
        )], ["Ons 5 aug 23:59"], old_reading],
        weather=old_reading, weather_time=1_000, weather_bucket=0,
        last_ntp=None,
    )
    monkeypatch.setattr(
        app.main, "_local_today_iso", lambda: clock["date"],
    )
    monkeypatch.setattr(
        app.main, "_local_now_strings",
        lambda: (("Tor 6 aug", "00:00") if clock["date"] == "2026-08-06"
                 else ("Ons 5 aug", "23:59")),
    )

    def settime():
        app.events.append("ntp")
        clock["date"] = "2026-08-06"

    monkeypatch.setattr(app.main.ntptime, "settime", settime)
    weather_fetches = []

    def fetch_today(*_args, **_kwargs):
        weather_fetches.append(True)
        return {
            "daily": {
                "time": ["2026-08-06"],
                "temperature_2m_max": [21], "temperature_2m_min": [11],
            },
            "hourly": {
                "time": ["2026-08-06T00:00", "2026-08-06T12:00"],
                "weather_code": [0, 0],
                "precipitation_probability": [0, 0],
            },
        }

    monkeypatch.setattr(app.main.openmeteo, "fetch_today", fetch_today)
    app.run_cycle(changed=False, state=state, connected=True, cfg=cfg)
    assert weather_fetches == [True]
    decoded = app.main.retained.decode(
        app.rtc.raw, app.main.retained.settings_fingerprint(cfg), 1,
    )
    assert decoded is not None
    assert decoded["frame"][1] == ["Tor 6 aug 00:00"]
    assert decoded["frame"][2]["date"] == "2026-08-06"


def test_expired_weather_is_not_shown_while_a_long_pull_interval_waits(
    app, monkeypatch,
):
    cfg = _valid_cfg({
        "enabled": True, "latitude": 59.33, "longitude": 18.06,
        "pull_interval_min": 300, "max_age_min": 180,
    })
    old_reading = {
        "date": "2026-08-06", "condition": "clear",
        "tmin": 10, "tmax": 20, "precip": 0,
    }
    state = dict(app.state_unchanged)
    state.update(
        settings=app.main.retained.settings_fingerprint(cfg),
        frame=[[_section(stale=False)], ["Tor 6 aug 12:00"], old_reading],
        weather=old_reading, weather_time=1_000 - 4 * 3600,
        weather_bucket=0, last_ntp=1_000,
    )
    fetches = []
    monkeypatch.setattr(
        app.main.openmeteo, "fetch_today",
        lambda *_args, **_kwargs: fetches.append(True),
    )
    app.run_cycle(changed=False, state=state, connected=True, cfg=cfg)
    assert fetches == []
    decoded = app.main.retained.decode(
        app.rtc.raw, app.main.retained.settings_fingerprint(cfg), 1,
    )
    assert decoded is not None
    assert decoded["frame"][2] == app.main.display.WEATHER_ERROR


def test_missing_credentials_commits_wifi_error_with_one_buffer_and_sleeps(app):
    app.run_main_once(wifi_cfg=None)
    assert "wifi-connect" not in app.events
    assert app.framebuf.sizes == [48_000]
    decoded = app.main.retained.decode(
        app.rtc.raw, app.main.retained.settings_fingerprint(app.cfg), 1,
    )
    assert decoded is not None
    assert decoded["frame"][2] == app.main.display.WIFI_ERROR
    assert app.machine.deepsleep_calls[-1] != 60_000


@pytest.mark.parametrize("error_type", ["settings", "config"])
def test_configuration_errors_do_not_enter_deep_sleep_recovery(app, error_type):
    if error_type == "settings":
        app.patch.setattr(
            app.main.settings, "load",
            lambda: (_ for _ in ()).throw(
                app.main.settings.SettingsError("bad settings")
            ),
        )
    else:
        app.patch.setattr(app.main.settings, "load", lambda: app.cfg)
        app.patch.setattr(
            app.main.config, "load",
            lambda: (_ for _ in ()).throw(
                app.main.config.ConfigError("bad config")
            ),
        )
    with pytest.raises((app.main.settings.SettingsError, app.main.config.ConfigError)):
        asyncio.run(app.main.main())
    assert app.machine.deepsleep_calls == []
    assert "rtc-empty" not in app.events
