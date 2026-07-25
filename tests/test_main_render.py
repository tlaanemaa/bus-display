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
    def __init__(self, fail=False):
        self.fail = fail

    def init(self):
        pass

    def display(self, _buf):
        if self.fail:
            raise OSError("panel write failed")

    def sleep(self):
        pass


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
