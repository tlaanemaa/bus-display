"""Isolated host tests for main.py's post-refresh serial corroboration."""
import builtins
import asyncio
import sys
import types
from pathlib import Path

import pytest


def _load_main_without_boot(monkeypatch):
    framebuf = types.ModuleType("framebuf")
    framebuf.MONO_HLSB = 0
    framebuf.FrameBuffer = object
    monkeypatch.setitem(sys.modules, "framebuf", framebuf)

    network = types.ModuleType("network")
    network.STA_IF = 0
    network.WLAN = object
    monkeypatch.setitem(sys.modules, "network", network)

    ntptime = types.ModuleType("ntptime")
    ntptime.settime = lambda: None
    monkeypatch.setitem(sys.modules, "ntptime", ntptime)

    machine = types.ModuleType("machine")
    machine.WDT = object
    machine.RTC = object
    machine.deepsleep = lambda _ms: None
    monkeypatch.setitem(sys.modules, "machine", machine)

    epd = types.ModuleType("epd7in5v2")
    epd.EPD7in5V2 = object
    monkeypatch.setitem(sys.modules, "epd7in5v2", epd)

    requests = types.ModuleType("requests")
    monkeypatch.setitem(sys.modules, "requests", requests)

    source = Path("src/main.py").read_text(encoding="utf-8")
    source = source.split("\ntry:\n    asyncio.run(main())", 1)[0]
    module = types.ModuleType("main_render_test")
    module.__file__ = "src/main.py"
    exec(compile(source, "src/main.py", "exec"), module.__dict__)
    return module


class _EPD:
    def __init__(self, fail=False, partial_fail=None, events=None):
        self.fail = fail
        self.partial_fail = partial_fail
        self.events = events

    def _record(self, event):
        if self.events is not None:
            self.events.append(event)

    def init(self):
        self._record("init")

    def display(self, _buf):
        self._record("display")
        if self.fail:
            raise OSError("panel write failed")

    def init_part(self):
        self._record("init_part")

    def partial_begin(self):
        self._record("partial_begin")

    def partial_old(self, _buf):
        self._record("partial_old")
        if self.partial_fail == "old":
            raise OSError("partial old failed")

    def partial_new(self, _buf):
        self._record("partial_new")
        if self.partial_fail == "new":
            raise OSError("partial new failed")

    def sleep(self):
        self._record("sleep")


class _RTC:
    def __init__(self, raw=b""):
        self.raw = raw

    def memory(self, raw=None):
        if raw is not None:
            self.raw = raw
        return self.raw


def _cfg():
    return {
        "stops": [{"name": "Slussen", "site_id": 9192}],
        "direction_code": 2,
        "forecast_min": 180,
        "departures_per_stop": 3,
        "data_pull_interval_min": 1,
        "render_interval_min": 1,
        "full_refresh_interval_min": 60,
        "power": {"deep_sleep": True, "wake_advance_s": 3},
        "weather": None,
    }


def _retained_state(main, cfg):
    return {
        "v": main.retained.RETAINED_VERSION,
        "render_rev": main.retained.RENDER_REVISION,
        "settings": main.retained.settings_fingerprint(cfg),
        "frame": {
            "sections": [{
                "stop_key": "9192:2",
                "name": "Slussen",
                "hero_main": None,
                "hero_unit": None,
                "badge_line": None,
                "dest": "No departures",
                "rows": [],
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


class _WDT:
    def feed(self):
        pass


class _WLAN:
    def active(self, _enabled):
        pass


def test_allocate_framebuffer_constructs_one_48000_byte_framebuffer(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    constructed = []
    framebuffer = object()

    monkeypatch.setattr(
        main.framebuf,
        "FrameBuffer",
        lambda buf, width, height, mode: constructed.append(
            (buf, width, height, mode)
        ) or framebuffer,
    )

    fb, fb_buf = main._allocate_framebuffer()

    assert fb is framebuffer
    assert len(fb_buf) == 48_000
    assert constructed == [
        (
            fb_buf,
            main._FB_WIDTH,
            main._FB_HEIGHT,
            main.framebuf.MONO_HLSB,
        ),
    ]


def test_deep_sleep_boot_allocates_before_rtc_wifi_ntp_and_reuses_objects(
    monkeypatch,
):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    events = []
    framebuffer = object()
    framebuffer_bytes = bytearray(1)
    cycle_args = []

    monkeypatch.setattr(
        main.settings,
        "load",
        lambda: events.append("settings") or cfg,
    )
    monkeypatch.setattr(
        main.config,
        "load",
        lambda: events.append("config")
        or {"wifi": {"ssid": "test", "password": "secret"}},
    )
    monkeypatch.setattr(
        main,
        "_allocate_framebuffer",
        lambda: events.append("allocate")
        or (framebuffer, framebuffer_bytes),
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "_rtc_state_load",
        lambda _cfg: events.append("rtc") or None,
    )
    monkeypatch.setattr(
        main.wifi,
        "connect_sta",
        lambda _ssid, _password: events.append("wifi") or True,
    )
    monkeypatch.setattr(
        main.ntptime,
        "settime",
        lambda: events.append("ntp"),
    )
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 0, raising=False)
    monkeypatch.setattr(main.time, "time", lambda: 120)
    monkeypatch.setattr(
        main.wake_schedule,
        "request_boundary",
        lambda _now, _interval: 180,
    )

    async def cycle_stub(*args):
        events.append("cycle")
        cycle_args.append(args)

    monkeypatch.setattr(main, "deep_sleep_cycle", cycle_stub)

    asyncio.run(main.main())

    assert events == [
        "settings",
        "config",
        "allocate",
        "rtc",
        "wifi",
        "ntp",
        "cycle",
    ]
    assert cycle_args[0][-2:] == (framebuffer, framebuffer_bytes)


def _prepare_deep_sleep_test(monkeypatch, main, now_epoch):
    saved = []
    refreshes = []

    async def no_wait(_wdt, _target):
        pass

    monkeypatch.setattr(main, "EPD7in5V2", lambda: _EPD())
    monkeypatch.setattr(main.machine, "WDT", lambda timeout: _WDT())
    monkeypatch.setattr(main.framebuf, "FrameBuffer", lambda *_args: object())
    monkeypatch.setattr(main, "_wait_until_epoch", no_wait)
    monkeypatch.setattr(main.time, "time", lambda: now_epoch)
    monkeypatch.setattr(main.time, "ticks_ms", lambda: 10, raising=False)
    monkeypatch.setattr(main.time, "ticks_diff", lambda a, b: a - b, raising=False)
    monkeypatch.setattr(main, "_local_now_strings", lambda: ("Lor 25 jul", "14:32"))
    monkeypatch.setattr(main, "_local_today_iso", lambda: "2026-07-25")
    monkeypatch.setattr(
        main,
        "_draw_and_refresh",
        lambda _epd, _fb, _buf, frame, old, full: refreshes.append(
            (frame, old, full)
        ),
    )
    monkeypatch.setattr(main, "_rtc_invalidate", lambda: None)

    def commit(raw, fingerprint, stop_keys):
        saved.append(main.retained.decode(raw, fingerprint, stop_keys))

    monkeypatch.setattr(main, "_rtc_commit", commit)
    monkeypatch.setattr(
        main.wake_schedule,
        "next_wake_delay_s",
        lambda _now, _advance, _interval: 57,
    )
    monkeypatch.setattr(main.network, "WLAN", lambda _kind: _WLAN())
    monkeypatch.setattr(main.machine, "deepsleep", lambda _ms: None)
    return saved, refreshes


def test_deep_sleep_cycle_reuses_supplied_framebuffer_without_allocating(
    monkeypatch,
):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    previous = _retained_state(main, cfg)
    supplied_fb = object()
    supplied_buf = builtins.bytearray(
        main._FB_WIDTH * main._FB_HEIGHT // 8
    )
    refresh_args = []
    _prepare_deep_sleep_test(monkeypatch, main, 200)

    def reject_framebuffer(*_args):
        raise AssertionError("deep_sleep_cycle constructed a second FrameBuffer")

    def reject_full_buffer(size=0):
        if size == main._FB_WIDTH * main._FB_HEIGHT // 8:
            raise AssertionError(
                "deep_sleep_cycle allocated a second full framebuffer"
            )
        return builtins.bytearray(size)

    def draw(_epd, fb, fb_buf, frame, old, full):
        refresh_args.append((fb, fb_buf, frame, old, full))

    monkeypatch.setattr(main.framebuf, "FrameBuffer", reject_framebuffer)
    monkeypatch.setattr(main, "bytearray", reject_full_buffer, raising=False)
    monkeypatch.setattr(main, "_draw_and_refresh", draw)

    asyncio.run(
        main.deep_sleep_cycle(
            cfg,
            None,
            False,
            previous,
            200,
            0,
            supplied_fb,
            supplied_buf,
        )
    )

    assert len(refresh_args) == 1
    assert refresh_args[0][0] is supplied_fb
    assert refresh_args[0][1] is supplied_buf


def test_rtc_load_uses_settings_and_stop_identity(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    expected = _retained_state(main, cfg)
    rtc = _RTC(main.retained.encode(expected))
    monkeypatch.setattr(main.machine, "RTC", lambda: rtc)

    assert main._rtc_state_load(cfg) == expected

    changed = dict(cfg)
    changed["direction_code"] = 1
    assert main._rtc_state_load(changed) is None


def test_rtc_load_rejects_old_v1_state_without_crashing(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    rtc = _RTC(main.retained.encode({"v": 1, "frame": []}))
    monkeypatch.setattr(main.machine, "RTC", lambda: rtc)

    assert main._rtc_state_load(_cfg()) is None


def test_rtc_save_verifies_using_current_compatibility_context(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    expected = _retained_state(main, cfg)
    rtc = _RTC()
    monkeypatch.setattr(main.machine, "RTC", lambda: rtc)

    main._rtc_state_save(expected, cfg)

    assert main.retained.decode(
        rtc.raw,
        main.retained.settings_fingerprint(cfg),
        ["9192:2"],
    ) == expected


def test_rtc_invalidate_writes_empty_bytes_and_verifies_readback(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    events = []

    class RTC:
        raw = b"old"

        def memory(self, raw=None):
            if raw is not None:
                events.append(("write", raw))
                self.raw = raw
            events.append(("read", self.raw))
            return self.raw

    monkeypatch.setattr(main.machine, "RTC", lambda: RTC())

    main._rtc_invalidate()

    assert events == [("write", b""), ("read", b""), ("read", b"")]


def test_rtc_invalidate_failure_stops_at_unverified_empty_write(monkeypatch):
    main = _load_main_without_boot(monkeypatch)

    class RTC:
        def memory(self, raw=None):
            return b"stale"

    monkeypatch.setattr(main.machine, "RTC", lambda: RTC())

    with pytest.raises(OSError, match="RTC retained-state invalidation failed"):
        main._rtc_invalidate()


def test_rtc_commit_checks_exact_bytes_and_strict_decode_context(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    expected = _retained_state(main, cfg)
    raw = main.retained.encode(expected)
    rtc = _RTC()
    monkeypatch.setattr(main.machine, "RTC", lambda: rtc)

    main._rtc_commit(
        raw,
        main.retained.settings_fingerprint(cfg),
        ["9192:2"],
    )

    assert rtc.raw == raw
    with pytest.raises(OSError, match="RTC retained-state readback mismatch"):
        main._rtc_commit(
            raw,
            main.retained.settings_fingerprint(cfg),
            ["1321:2"],
        )
    assert rtc.raw == b""


@pytest.mark.parametrize(
    ("failure_stage", "error_type", "message"),
    [
        ("write", OSError, "commit write failed"),
        ("readback", OSError, "commit readback failed"),
        ("decode", RuntimeError, "commit decode failed"),
    ],
)
def test_rtc_commit_exceptions_attempt_verified_clear_and_reraise_original(
    monkeypatch, failure_stage, error_type, message,
):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    expected = _retained_state(main, cfg)
    raw = main.retained.encode(expected)
    events = []

    class RTC:
        stored = b"old"
        fail_readback = False

        def memory(self, value=None):
            if value is not None:
                events.append(("write", value))
                if value == raw and failure_stage == "write":
                    raise OSError(message)
                self.stored = value
                if value == raw and failure_stage == "readback":
                    self.fail_readback = True
                return self.stored
            events.append(("read", self.stored))
            if self.fail_readback:
                self.fail_readback = False
                raise OSError(message)
            return self.stored

    rtc = RTC()
    monkeypatch.setattr(main.machine, "RTC", lambda: rtc)
    if failure_stage == "decode":
        monkeypatch.setattr(
            main.retained,
            "decode",
            lambda *_args: (_ for _ in ()).throw(RuntimeError(message)),
        )

    with pytest.raises(error_type, match=message):
        main._rtc_commit(
            raw,
            main.retained.settings_fingerprint(cfg),
            ["9192:2"],
        )

    assert events[-2:] == [("write", b""), ("read", b"")]
    assert rtc.stored == b""


def test_rtc_commit_cleanup_failure_is_reported_without_masking_original(
    monkeypatch,
):
    main = _load_main_without_boot(monkeypatch)
    printed = []

    class RTC:
        def memory(self, value=None):
            if value == b"encoded":
                raise OSError("commit write failed")
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(main.machine, "RTC", lambda: RTC())
    monkeypatch.setattr(
        builtins,
        "print",
        lambda *parts: printed.append(" ".join(str(part) for part in parts)),
    )

    with pytest.raises(OSError, match="commit write failed"):
        main._rtc_commit(b"encoded", "fingerprint", ["9192:2"])

    assert printed == [
        "retained: cleanup after failed commit also failed: cleanup failed",
    ]


def test_deep_sleep_policy_does_not_reuse_a_reconfigured_stop_by_position(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    cfg["stops"] = [{"name": "Nacka", "site_id": 1234}]
    old_cfg = _cfg()
    previous = _retained_state(main, old_cfg)
    previous["frame"]["sections"][0]["dest"] = "Old departure"
    previous["last_ntp"] = 199
    saved, refreshes = _prepare_deep_sleep_test(monkeypatch, main, 200)
    monkeypatch.setattr(main, "_fetch_all_stops", lambda *_args, **_kwargs: [None])

    asyncio.run(
        main.deep_sleep_cycle(
            cfg, None, True, previous, 200, 0, object(), bytearray()
        )
    )

    assert saved[0]["frame"]["sections"][0]["dest"] == "No departures"
    assert saved[0]["frame"]["sections"][0]["stale"] is True
    assert refreshes[0][2] is True


def test_deep_sleep_weather_future_timestamp_forces_an_adapter_attempt(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    cfg["weather"] = {
        "enabled": True,
        "latitude": 59.33,
        "longitude": 18.06,
        "pull_interval_min": 30,
        "max_age_min": 180,
    }
    previous = _retained_state(main, cfg)
    previous["weather"] = {
        "date": "2026-07-25",
        "condition": "cloudy",
        "tmin": 10,
        "tmax": 20,
        "precip": 20,
    }
    previous["weather_time"] = 200
    previous["last_ntp"] = 99
    fetched = {
        "date": "2026-07-25",
        "condition": "clear",
        "tmin": 12,
        "tmax": 21,
        "precip": 5,
    }
    saved, _refreshes = _prepare_deep_sleep_test(monkeypatch, main, 100)
    monkeypatch.setattr(main, "_fetch_all_stops", lambda *_args, **_kwargs: [[]])
    monkeypatch.setattr(main.openmeteo, "fetch_today", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main.weather, "parse_weather", lambda _raw: fetched)

    asyncio.run(
        main.deep_sleep_cycle(
            cfg, None, True, previous, 100, 0, object(), bytearray()
        )
    )

    assert saved[0]["weather"] == fetched
    assert saved[0]["weather_time"] == 100


def test_changed_deep_sleep_applies_encode_invalidate_refresh_commit_order(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    previous = _retained_state(main, cfg)
    proposed = _retained_state(main, cfg)
    proposed["frame"]["footer"] = ["Lor 25 jul 14:33"]
    events = []
    _prepare_deep_sleep_test(monkeypatch, main, 200)
    monkeypatch.setattr(
        main.cycle,
        "decide",
        lambda *_args: {
            "frame": proposed["frame"],
            "refresh": "partial",
            "state": proposed,
        },
    )
    monkeypatch.setattr(
        main.retained,
        "encode",
        lambda state: events.append(("encode", state)) or b"encoded",
    )
    monkeypatch.setattr(
        main,
        "_rtc_invalidate",
        lambda: events.append("invalidate"),
    )
    monkeypatch.setattr(
        main,
        "EPD7in5V2",
        lambda: events.append("construct_epd") or _EPD(),
    )
    monkeypatch.setattr(
        main,
        "_draw_and_refresh",
        lambda *_args, **_kwargs: events.append("refresh"),
    )
    monkeypatch.setattr(
        main,
        "_rtc_commit",
        lambda raw, fingerprint, stop_keys: events.append(
            ("commit", raw, fingerprint, stop_keys)
        ),
    )

    asyncio.run(
        main.deep_sleep_cycle(
            cfg, None, False, previous, 200, 0, object(), bytearray()
        )
    )

    assert events == [
        ("encode", proposed),
        "invalidate",
        "construct_epd",
        "refresh",
        (
            "commit",
            b"encoded",
            main.retained.settings_fingerprint(cfg),
            ["9192:2"],
        ),
    ]


def test_unchanged_deep_sleep_commits_without_invalidation_or_panel(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    previous = _retained_state(main, cfg)
    proposed = _retained_state(main, cfg)
    proposed["last_ntp"] = 200
    events = []
    _prepare_deep_sleep_test(monkeypatch, main, 200)
    monkeypatch.setattr(
        main.cycle,
        "decide",
        lambda *_args: {
            "frame": previous["frame"],
            "refresh": "none",
            "state": proposed,
        },
    )
    monkeypatch.setattr(
        main.retained,
        "encode",
        lambda state: events.append(("encode", state)) or b"encoded",
    )
    monkeypatch.setattr(
        main,
        "_rtc_invalidate",
        lambda: events.append("invalidate"),
    )
    monkeypatch.setattr(
        main,
        "EPD7in5V2",
        lambda: events.append("construct_epd") or _EPD(),
    )
    monkeypatch.setattr(
        main,
        "_draw_and_refresh",
        lambda *_args, **_kwargs: events.append("refresh"),
    )
    monkeypatch.setattr(
        main,
        "_rtc_commit",
        lambda raw, fingerprint, stop_keys: events.append(
            ("commit", raw, fingerprint, stop_keys)
        ),
    )

    asyncio.run(
        main.deep_sleep_cycle(
            cfg, None, False, previous, 200, 0, object(), bytearray()
        )
    )

    assert events == [
        ("encode", proposed),
        (
            "commit",
            b"encoded",
            main.retained.settings_fingerprint(cfg),
            ["9192:2"],
        ),
    ]


def test_encode_failure_does_not_construct_epd_or_touch_rtc(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    cfg = _cfg()
    previous = _retained_state(main, cfg)
    proposed = _retained_state(main, cfg)
    proposed["frame"]["footer"] = ["Lor 25 jul 14:33"]
    events = []
    _prepare_deep_sleep_test(monkeypatch, main, 200)
    monkeypatch.setattr(
        main.cycle,
        "decide",
        lambda *_args: {
            "frame": proposed["frame"],
            "refresh": "partial",
            "state": proposed,
        },
    )
    monkeypatch.setattr(
        main.retained,
        "encode",
        lambda _state: events.append("encode")
        or (_ for _ in ()).throw(ValueError("oversize")),
    )
    monkeypatch.setattr(
        main,
        "EPD7in5V2",
        lambda: events.append("construct_epd") or _EPD(),
    )
    monkeypatch.setattr(
        main,
        "_rtc_invalidate",
        lambda: events.append("invalidate"),
    )
    monkeypatch.setattr(
        main,
        "_rtc_commit",
        lambda *_args: events.append("commit"),
    )

    with pytest.raises(ValueError, match="oversize"):
        asyncio.run(
            main.deep_sleep_cycle(
                cfg, None, False, previous, 200, 0, object(), bytearray()
            )
        )

    assert events == ["encode"]


def test_successful_refresh_logs_only_the_new_frame(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    draws = []
    printed = []
    new_frame = ([{"name": "new"}], ["footer"], {"kind": "none"})
    old_frame = ([{"name": "old"}], ["footer"], {"kind": "none"})
    monkeypatch.setattr(main.display, "draw_home", lambda _fb, *frame: draws.append(frame))
    monkeypatch.setattr(main.display, "frame_summary", lambda frame: "summary:" + frame["sections"][0]["name"])
    monkeypatch.setattr(builtins, "print", lambda text: printed.append(text))

    main._draw_and_refresh(_EPD(), object(), bytearray(), new_frame, old_frame, full=True)

    assert draws == [new_frame]
    assert printed == ["summary:new"]


def test_successful_refresh_accepts_explicit_display_frame(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    draws = []
    printed = []
    frame = {
        "sections": [{"name": "new"}],
        "footer": ["footer"],
        "status": {"kind": "none"},
    }
    monkeypatch.setattr(
        main.display, "draw_home",
        lambda _fb, supplied: draws.append(supplied),
    )
    monkeypatch.setattr(
        main.display, "frame_summary",
        lambda supplied: "summary:" + supplied["sections"][0]["name"],
    )
    monkeypatch.setattr(builtins, "print", lambda text: printed.append(text))

    main._draw_and_refresh(_EPD(), object(), bytearray(), frame, None, full=True)

    assert draws == [frame]
    assert printed == ["summary:new"]


def test_failed_refresh_does_not_log_a_success_summary(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    printed = []
    frame = ([{"name": "new"}], ["footer"], {"kind": "none"})
    monkeypatch.setattr(main.display, "draw_home", lambda _fb, *_frame: None)
    monkeypatch.setattr(main.display, "frame_summary", lambda _frame: "summary:new")
    monkeypatch.setattr(builtins, "print", lambda text: printed.append(text))

    with pytest.raises(OSError, match="panel write failed"):
        main._draw_and_refresh(_EPD(fail=True), object(), bytearray(), frame, None, full=True)

    assert printed == []


def test_successful_partial_sends_old_then_new_and_logs_only_new(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    events = []
    new_frame = ([{"name": "new"}], ["footer"], {"kind": "none"})
    old_frame = ([{"name": "old"}], ["footer"], {"kind": "none"})
    monkeypatch.setattr(main.display, "draw_home", lambda _fb, *frame: events.append("draw:" + frame[0][0]["name"]))
    monkeypatch.setattr(main.display, "frame_summary", lambda frame: "summary:" + frame["sections"][0]["name"])
    monkeypatch.setattr(builtins, "print", lambda text: events.append("print:" + text))

    main._draw_and_refresh(_EPD(events=events), object(), bytearray(), new_frame, old_frame, full=False)

    assert events == [
        "init_part", "partial_begin", "draw:old", "partial_old",
        "draw:new", "partial_new", "sleep", "print:summary:new",
    ]


def test_successful_partial_accepts_explicit_display_frames(monkeypatch):
    main = _load_main_without_boot(monkeypatch)
    events = []
    new_frame = {
        "sections": [{"name": "new"}],
        "footer": ["footer"],
        "status": {"kind": "none"},
    }
    old_frame = {
        "sections": [{"name": "old"}],
        "footer": ["footer"],
        "status": {"kind": "none"},
    }
    monkeypatch.setattr(
        main.display, "draw_home",
        lambda _fb, supplied: events.append(
            "draw:" + supplied["sections"][0]["name"],
        ),
    )
    monkeypatch.setattr(
        main.display, "frame_summary",
        lambda supplied: "summary:" + supplied["sections"][0]["name"],
    )
    monkeypatch.setattr(builtins, "print", lambda text: events.append("print:" + text))

    main._draw_and_refresh(
        _EPD(events=events), object(), bytearray(),
        new_frame, old_frame, full=False,
    )

    assert events == [
        "init_part", "partial_begin", "draw:old", "partial_old",
        "draw:new", "partial_new", "sleep", "print:summary:new",
    ]


@pytest.mark.parametrize(
    ("partial_fail", "expected_events"),
    [
        ("old", ["init_part", "partial_begin", "draw:old", "partial_old", "sleep"]),
        ("new", ["init_part", "partial_begin", "draw:old", "partial_old", "draw:new", "partial_new", "sleep"]),
    ],
)
def test_failed_partial_plane_never_logs_a_success_summary(monkeypatch, partial_fail, expected_events):
    main = _load_main_without_boot(monkeypatch)
    events = []
    new_frame = ([{"name": "new"}], ["footer"], {"kind": "none"})
    old_frame = ([{"name": "old"}], ["footer"], {"kind": "none"})
    monkeypatch.setattr(main.display, "draw_home", lambda _fb, *frame: events.append("draw:" + frame[0][0]["name"]))
    monkeypatch.setattr(main.display, "frame_summary", lambda _frame: "summary:new")
    monkeypatch.setattr(builtins, "print", lambda text: events.append("print:" + text))

    with pytest.raises(OSError, match="partial %s failed" % partial_fail):
        main._draw_and_refresh(
            _EPD(partial_fail=partial_fail, events=events), object(), bytearray(),
            new_frame, old_frame, full=False)

    assert events == expected_events
