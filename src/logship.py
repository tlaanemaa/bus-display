"""Ship log lines to a VictoriaLogs server on the LAN (added 2026-11-14).

The flash field log (fieldlog.py) stays the durable black box: it survives
power loss and a dead network, but you have to unplug the display to read it.
This module ALSO sends lines over the LAN to VictoriaLogs (the owner's
Raspberry Pi), so the device can be watched without touching it, and so we can
afford lines the flash log can't (a per-minute status line costs no flash wear).

Shape (chosen for this PSRAM-less board's ~3-5 KB of spare IDF/network RAM,
see CLAUDE.md "RAM budget"):
  - add() only appends to a small RAM queue (MAX_QUEUE lines, oldest dropped
    and counted). Never touches the network.
  - flush() runs once per network slot: ONE plain-HTTP POST of all queued
    lines to /insert/jsonline, to a fixed IP:port -- no DNS (DNS is exactly
    what fails during the outages we want to see), no TLS, short timeout.
    On any failure the lines stay queued for the next slot. Never raises.
  - Lines lost to a crash between flushes are at most ~1 minute's worth; the
    important ones are on flash anyway.

Each line is one JSON object: _msg, app, kind ("event" = a field-log line,
"tick" = per-minute status, "render"), level (error/warn/info/debug, see
level_for), boot (an id per boot, to group a session), and _time. Field-log events and render lines carry the DEVICE clock
as _time (they sit in the queue until the next network slot, or longer during
an outage). Ticks carry NO _time, so VictoriaLogs
stamps them on arrival, plus the device clock as dev_time -- ticks are
flushed within a second of being queued, so dev_time - _time is the device
clock's error (the drift question from the first field-log review).

Pure except flush()'s socket I/O, which takes an injectable socket factory, so
the queue/format/HTTP logic runs under host pytest (CLAUDE.md "Testability
rule").
"""
import json

APP = "bus-display"
MAX_QUEUE = 20       # lines held in RAM between flushes (~1 min normally; an outage fills it)
MAX_MSG = 700        # longer messages (tracebacks) are truncated
TIMEOUT_S = 3        # connect + send + first byte of the reply, per flush (WDT budget)
PATH = "/insert/jsonline?_stream_fields=app"

_q = []              # [(kind, msg, time_rfc3339_or_None, dev_time_or_None)]
_st = {"dropped": 0, "enabled": None, "host": None, "port": None, "boot": "", "fails": 0}
# enabled: None = not configured yet (queue, so BOOT lines before settings load
# aren't lost), False = disabled (drop everything), True = shipping.


def configure(url, boot_id=""):
    """url like 'http://192.168.1.100:9428' (falsy -> disabled). IP literal
    expected: a hostname would need DNS on every flush."""
    _st["boot"] = boot_id
    hp = parse_url(url) if url else None
    if hp is None:
        _st["enabled"] = False
        del _q[:]
        return False
    _st["host"], _st["port"] = hp
    _st["enabled"] = True
    return True


def parse_url(url):
    """'http://host[:port][/]' -> (host, port), or None if not plain http."""
    if not url.startswith("http://"):
        return None
    rest = url[7:].split("/", 1)[0]
    if not rest:
        return None
    if ":" in rest:
        host, port = rest.rsplit(":", 1)
        try:
            return host, int(port)
        except ValueError:
            return None
    return rest, 80


def add(msg, kind="event", t=None, dev_time=None):
    """Queue one line. t: RFC3339 UTC string for _time (None = let the server
    stamp it on arrival). Cheap, never raises, never touches the network."""
    if _st["enabled"] is False:
        return
    try:
        if len(msg) > MAX_MSG:
            msg = msg[:MAX_MSG] + "...[truncated]"
        if len(_q) >= MAX_QUEUE:
            _q.pop(0)
            _st["dropped"] += 1
        _q.append((kind, msg, t, dev_time))
    except Exception:
        pass


def level_for(kind, msg):
    """Log level for VictoriaLogs' `level` field (its UI colours and filters on
    it): error / warn / info / debug. Derived from the message so the ~20
    fieldlog.event() call sites don't each need a level argument. Rules match
    the field-log vocabulary (CLAUDE.md "Field log"):
      error: crashes and resets nobody asked for -- FATAL, unexpected
             exceptions, watchdog resets, SELF-HEAL resets
      warn:  something degraded or an unexplained HARD_RESET (a C-level panic
             is indistinguishable from machine.reset(), see CLAUDE.md), a stop
             DOWN, failed reconnect/NTP, weather error, dropped/suppressed lines
      info:  normal state changes, heartbeats, per-minute ticks
      debug: render lines (one per minute, the screen's text)"""
    if kind == "render":
        return "debug"
    if kind == "tick":
        return "warn" if "FAIL" in msg else "info"
    if msg.startswith("BOOT "):
        if "cause=WDT_RESET" in msg:
            return "error"
        if "cause=HARD_RESET" in msg and "last_phase=selfheal" not in msg:
            return "warn"
        return "info"
    if msg.startswith(("FATAL", "SELF-HEAL", "unexpected")) or "Traceback" in msg:
        return "error"
    for w in (" DOWN:", "FAILED", "ERROR", "raised", "dropped", "suppressed"):
        if w in msg:
            return "warn"
    return "info"


def pending():
    return len(_q)


def encode_line(kind, msg, t, dev_time, boot):
    d = {"_msg": msg, "app": APP, "kind": kind, "level": level_for(kind, msg), "boot": boot}
    if t:
        d["_time"] = t
    if dev_time:
        d["dev_time"] = dev_time
    return (json.dumps(d) + "\n").encode()


def build_request(host, port, lines):
    """(header_bytes, [line_bytes]) for one POST of the given encoded lines."""
    n = 0
    for b in lines:
        n += len(b)
    hdr = ("POST %s HTTP/1.1\r\nHost: %s:%d\r\nContent-Type: application/stream+json\r\n"
           "Content-Length: %d\r\nConnection: close\r\n\r\n" % (PATH, host, port, n)).encode()
    return hdr, lines


def parse_status(first):
    """HTTP status code from the first bytes of a reply, or 0 if unreadable."""
    try:
        parts = first.split(b" ", 2)
        if parts[0].startswith(b"HTTP/"):
            return int(parts[1])
    except Exception:
        pass
    return 0


def _default_socket(host, port):
    import socket
    s = socket.socket()
    s.settimeout(TIMEOUT_S)
    try:
        s.connect(socket.getaddrinfo(host, port)[0][-1])
    except Exception:
        s.close()
        raise
    return s


def _send_all(s, data):
    mv = memoryview(data)
    while mv:
        n = s.send(mv)
        if not n:
            raise OSError("send returned 0")
        mv = mv[n:]


def flush(sock_factory=None):
    """POST everything queued. Returns True (sent), False (failed; lines kept)
    or None (nothing to do / disabled). Never raises."""
    if not _st["enabled"] or not _q:
        return None
    if _st["dropped"]:
        # Say so in the stream, ahead of what's left.
        _q.insert(0, ("event", "logship: %d lines dropped (queue full)" % _st["dropped"], None, None))
        if len(_q) > MAX_QUEUE:
            _q.pop()
            # The newest line is lost to make room for the notice; still counted.
        _st["dropped"] = 0
    batch = len(_q)
    s = None
    try:
        lines = [encode_line(k, m, t, dt, _st["boot"]) for (k, m, t, dt) in _q[:batch]]
        hdr, lines = build_request(_st["host"], _st["port"], lines)
        s = (sock_factory or _default_socket)(_st["host"], _st["port"])
        _send_all(s, hdr)
        for b in lines:
            _send_all(s, b)
        code = parse_status(s.recv(16))
        if not 200 <= code < 300:
            raise OSError("HTTP %d" % code)
        del _q[:batch]
        _st["fails"] = 0
        return True
    except Exception as e:
        _st["fails"] += 1
        print("logship: flush failed (%d queued): %s" % (len(_q), e))
        return False
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def rfc3339(tm):
    """time.gmtime()-style tuple -> '2026-11-14T10:26:12Z', or None if the
    clock was never set (year < 2025 -> let the server stamp it instead)."""
    if tm[0] < 2025:
        return None
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % tuple(tm[:6])


# Entry points used by app.py (kept here, not in app.py, so their bytecode is
# only in RAM once this module is imported -- AFTER the first fetch; see
# CLAUDE.md "Log shipping" for why that order matters).

def event(msg, tm=None):
    """fieldlog.sink: a field-log line, stamped with the device clock (tm =
    the time.gmtime() it happened, for lines buffered before import)."""
    import time
    add(msg, t=rfc3339(tm or time.gmtime()))


def tick(msg):
    """Per-minute status, ship-only. No _time: the server stamps it on arrival
    (flushed ~1 s after queueing), and dev_time carries the device clock, so
    dev_time - _time ~= the device clock's error."""
    import time
    add(msg, kind="tick", dev_time=rfc3339(time.gmtime()))


def render(msg):
    """What the panel now shows. Device-stamped: it waits ~20 s for the next
    network slot."""
    import time
    add(msg, kind="render", t=rfc3339(time.gmtime()))


def rssi():
    try:
        import network
        return network.WLAN(network.STA_IF).status("rssi")
    except Exception:
        return "?"


def boot_id():
    try:
        import os
        return "".join("%02x" % b for b in os.urandom(4))
    except Exception:
        return "?"
