"""Host tests for the flash event log (fieldlog.py): appends, rotation cap,
never-raises, tracebacks captured."""
import pytest
import fieldlog


@pytest.fixture(autouse=True)
def _fresh_rate_cap(monkeypatch):
    # The hourly cap is module state; give every test a clean, roomy budget.
    monkeypatch.setattr(fieldlog, "_rate", {"hour": None, "count": 0, "suppressed": 0})
    monkeypatch.setattr(fieldlog, "MAX_LINES_PER_HOUR", 10000)


def _paths(tmp_path):
    return str(tmp_path / "log.txt"), str(tmp_path / "log.old.txt")


def test_event_appends_timestamped_lines(tmp_path, monkeypatch):
    p, old = _paths(tmp_path)
    monkeypatch.setattr(fieldlog, "stamp", lambda: "2026-11-08 12:00:00")
    fieldlog.event("boot cause=WDT", p, old)
    fieldlog.event("stop A down: ETIMEDOUT", p, old)
    assert open(p).read() == ("2026-11-08 12:00:00 boot cause=WDT\n"
                              "2026-11-08 12:00:00 stop A down: ETIMEDOUT\n")


def test_rotation_caps_total_size_and_keeps_newest(tmp_path):
    p, old = _paths(tmp_path)
    for i in range(500):
        fieldlog.event("line %03d %s" % (i, "x" * 40), p, old, max_bytes=2000)
    import os
    assert os.path.getsize(p) <= 2000 and os.path.getsize(old) <= 2000
    assert "line 499" in open(p).read()          # newest kept
    assert "line 000" not in open(p).read() + open(old).read()  # oldest rotated away


def test_event_never_raises_on_unwritable_path(tmp_path):
    fieldlog.event("hello", str(tmp_path / "no_such_dir" / "log.txt"), str(tmp_path / "x"))


def test_exception_logs_traceback(tmp_path):
    p, old = _paths(tmp_path)
    try:
        {}["missing"]
    except Exception as e:
        fieldlog.exception("display_loop crashed", e, p, old)
    text = open(p).read()
    assert "display_loop crashed" in text and "KeyError" in text and "test_fieldlog.py" in text


def test_hourly_rate_cap_suppresses_and_reports(tmp_path, monkeypatch):
    # Flash-wear guard: a log-every-tick bug can't write more than the cap.
    p, old = _paths(tmp_path)
    hour = {"h": 100}
    monkeypatch.setattr(fieldlog, "_hour", lambda: hour["h"])
    monkeypatch.setattr(fieldlog, "MAX_LINES_PER_HOUR", 30)
    for i in range(100):
        fieldlog.event("spam %d" % i, p, old)
    fieldlog.event("BOOT", p, old, force=True)          # forced lines always land
    assert open(p).read().count("spam") == fieldlog.MAX_LINES_PER_HOUR
    assert "BOOT" in open(p).read()
    hour["h"] = 101
    fieldlog.event("next hour", p, old)
    text = open(p).read()
    assert "70 log lines suppressed" in text and "next hour" in text
