"""Persistent event log on the device's flash -- the field "black box".

The kitchen unit runs with no serial attached, so print() output is lost.
This appends RARE, meaningful events (boots + reset cause, fetch failures and
recoveries, reconnects, self-heal resets, crashes with tracebacks, an hourly
heartbeat) to /log.txt, which survives resets AND power loss. Pull it over
USB whenever: `tools\\get_logs.bat` (see CLAUDE.md "Field log").

Bounded two ways:
  - SIZE: when /log.txt passes MAX_BYTES it is rotated to /log.old.txt
    (replacing the previous one), so the log never uses more than ~2x
    MAX_BYTES of flash and the newest ~MAX_BYTES..2x MAX_BYTES is kept.
  - WEAR: only state CHANGES are logged by the callers (a stop going down,
    coming back), never "fetch ok" every minute. Normal operation writes about
    one heartbeat line per hour; LittleFS spreads those writes across the free
    flash, so wear is negligible for the device's lifetime.

Each write opens/appends/closes, so a line is on flash the moment event()
returns -- nothing waits in a RAM buffer to be lost on a WDT reset.

No machine/network imports (only os + the injectable clock), so it runs under
host pytest (CLAUDE.md "Testability rule").
"""
import os

PATH = "/log.txt"
OLD_PATH = "/log.old.txt"
MAX_BYTES = 24 * 1024

# Set by main.py once the clock is known: a function returning the timestamp
# prefix string. Default is "?" so logging works even before that.
stamp = lambda: "?"

# Flash-wear guard: at most MAX_LINES_PER_HOUR lines per wall-clock hour reach
# flash. A bug that logs every tick would otherwise write ~1440 lines/day;
# with the cap the worst case is 24*30 = 720/day, and the overflow is counted
# and reported as one "suppressed" line. force=True (BOOT, FATAL) bypasses it.
MAX_LINES_PER_HOUR = 30
_rate = {"hour": None, "count": 0, "suppressed": 0}


def _hour():
    import time
    return int(time.time() // 3600)


def _size(path):
    try:
        return os.stat(path)[6]
    except OSError:
        return 0


def _rotate(path, old_path):
    try:
        os.remove(old_path)
    except OSError:
        pass
    try:
        os.rename(path, old_path)
    except OSError:
        pass


def event(msg, path=None, old_path=None, max_bytes=None, force=False):
    """Append one timestamped line (also printed, for the bench). Never
    raises: a full/broken filesystem must not take the display down."""
    path = path or PATH
    old_path = old_path or OLD_PATH
    max_bytes = max_bytes or MAX_BYTES
    try:
        line = "%s %s\n" % (stamp(), msg)
    except Exception:
        line = "? %s\n" % msg
    print("log:", line, end="")
    try:
        h = _hour()
    except Exception:
        h = None
    if h != _rate["hour"]:
        if _rate["suppressed"]:
            _write("%s (%d log lines suppressed last hour: rate cap)\n" % (stamp(), _rate["suppressed"]),
                   path, old_path, max_bytes)
        _rate["hour"], _rate["count"], _rate["suppressed"] = h, 0, 0
    if not force and _rate["count"] >= MAX_LINES_PER_HOUR:
        _rate["suppressed"] += 1
        return
    _rate["count"] += 1
    _write(line, path, old_path, max_bytes)


def _write(line, path, old_path, max_bytes):
    try:
        if _size(path) + len(line) > max_bytes:
            _rotate(path, old_path)
        with open(path, "a") as f:
            f.write(line)
    except Exception as e:
        print("log: write failed:", e)


def exception(context, exc, path=None, old_path=None, max_bytes=None, force=False):
    """Log an exception WITH its traceback (MicroPython: sys.print_exception;
    CPython: traceback) -- the single most useful thing for fixing a crash."""
    tb = ""
    try:
        import sys
        if hasattr(sys, "print_exception"):
            import io
            buf = io.StringIO()
            sys.print_exception(exc, buf)
            tb = buf.getvalue()
        else:
            import traceback
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        tb = repr(exc)
    event("%s: %r\n  %s" % (context, exc, tb.strip().replace("\n", "\n  ")),
          path, old_path, max_bytes, force=force)
