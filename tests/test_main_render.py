"""Isolated host tests for main.py's post-refresh serial corroboration."""
import builtins
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
