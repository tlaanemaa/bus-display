"""Host tests for logship.py: queue bounds, wire format, flush success/failure
with a fake socket, never-raises."""
import json
import pytest
import logship
import fieldlog


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(logship, "_q", [])
    monkeypatch.setattr(logship, "_st", {"dropped": 0, "enabled": None, "host": None,
                                         "port": None, "boot": "", "fails": 0})


class FakeSock:
    def __init__(self, reply=b"HTTP/1.1 200 OK\r\n", fail_send=False):
        self.sent = b""
        self.reply = reply
        self.fail_send = fail_send
        self.closed = False

    def send(self, data):
        if self.fail_send:
            raise OSError("ECONNRESET")
        data = bytes(data)[:7]  # partial sends, to exercise the send loop
        self.sent += data
        return len(data)

    def recv(self, n):
        return self.reply[:n]

    def close(self):
        self.closed = True


def _split(sent):
    hdr, body = sent.split(b"\r\n\r\n", 1)
    return hdr.decode(), [json.loads(l) for l in body.decode().splitlines()]


def test_parse_url():
    assert logship.parse_url("http://192.168.1.100:9428") == ("192.168.1.100", 9428)
    assert logship.parse_url("http://192.168.1.100:9428/") == ("192.168.1.100", 9428)
    assert logship.parse_url("http://10.0.0.2") == ("10.0.0.2", 80)
    assert logship.parse_url("https://x:1") is None
    assert logship.parse_url("http://h:notaport") is None


def test_lines_queued_before_configure_are_shipped_after():
    logship.add("BOOT cause=PWRON_RESET")
    assert logship.configure("http://192.168.1.100:9428", "b1")
    sock = FakeSock()
    assert logship.flush(lambda h, p: sock) is True
    hdr, lines = _split(sock.sent)
    assert hdr.startswith("POST /insert/jsonline?_stream_fields=app HTTP/1.1")
    assert "Host: 192.168.1.100:9428" in hdr
    assert lines == [{"_msg": "BOOT cause=PWRON_RESET", "app": "bus-display", "kind": "event",
                      "level": "info", "boot": "b1"}]
    assert logship.pending() == 0
    assert sock.closed


def test_content_length_matches_body():
    logship.configure("http://h:1")
    logship.add("ünïcode line °", t="2026-11-14T10:00:00Z")
    logship.add("tick", kind="tick", dev_time="2026-11-14T10:00:01Z")
    sock = FakeSock()
    logship.flush(lambda h, p: sock)
    hdr, body = sock.sent.split(b"\r\n\r\n", 1)
    cl = [l for l in hdr.decode().split("\r\n") if l.startswith("Content-Length")][0]
    assert int(cl.split(": ")[1]) == len(body)
    lines = [json.loads(l) for l in body.decode().splitlines()]
    assert lines[0]["_time"] == "2026-11-14T10:00:00Z" and "dev_time" not in lines[0]
    assert lines[1]["kind"] == "tick" and lines[1]["dev_time"] == "2026-11-14T10:00:01Z"
    assert "_time" not in lines[1]  # server stamps ticks on arrival


def test_disabled_drops_everything():
    logship.add("before")
    assert logship.configure("") is False
    logship.add("after")
    assert logship.pending() == 0
    assert logship.flush(lambda h, p: FakeSock()) is None


def test_failure_keeps_lines_and_never_raises():
    logship.configure("http://h:1")
    logship.add("a")
    logship.add("b")
    assert logship.flush(lambda h, p: FakeSock(fail_send=True)) is False
    assert logship.pending() == 2

    def boom(h, p):
        raise OSError("EHOSTUNREACH")
    assert logship.flush(boom) is False
    assert logship.flush(lambda h, p: FakeSock(reply=b"HTTP/1.1 500 Oops\r\n")) is False
    assert logship.pending() == 2
    sock = FakeSock()
    assert logship.flush(lambda h, p: sock) is True
    assert [l["_msg"] for l in _split(sock.sent)[1]] == ["a", "b"]


def test_queue_is_bounded_and_reports_drops():
    logship.configure("http://h:1")
    for i in range(logship.MAX_QUEUE + 5):
        logship.add("line %d" % i)
    assert logship.pending() == logship.MAX_QUEUE
    sock = FakeSock()
    logship.flush(lambda h, p: sock)
    msgs = [l["_msg"] for l in _split(sock.sent)[1]]
    assert msgs[0] == "logship: 5 lines dropped (queue full)"
    assert len(msgs) == logship.MAX_QUEUE
    assert msgs[1] == "line 5"  # oldest dropped first


def test_long_messages_truncated():
    logship.configure("http://h:1")
    logship.add("x" * 5000)
    assert len(logship._q[0][1]) < logship.MAX_MSG + 20


def test_parse_status():
    assert logship.parse_status(b"HTTP/1.1 204 No") == 204
    assert logship.parse_status(b"") == 0
    assert logship.parse_status(b"garbage") == 0


def test_rfc3339():
    assert logship.rfc3339((2026, 11, 4, 9, 5, 3, 2, 308)) == "2026-11-04T09:05:03Z"


def test_fieldlog_sink_gets_every_line_even_past_rate_cap(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(fieldlog, "sink", got.append)
    monkeypatch.setattr(fieldlog, "_rate", {"hour": None, "count": 0, "suppressed": 0})
    monkeypatch.setattr(fieldlog, "MAX_LINES_PER_HOUR", 1)
    p, old = str(tmp_path / "l.txt"), str(tmp_path / "o.txt")
    fieldlog.event("one", p, old)
    fieldlog.event("two", p, old)
    assert got == ["one", "two"]
    assert open(p).read().count("\n") == 1  # flash still capped


def test_fieldlog_sink_errors_are_swallowed(tmp_path, monkeypatch):
    def bad(msg):
        raise RuntimeError("nope")
    monkeypatch.setattr(fieldlog, "sink", bad)
    p, old = str(tmp_path / "l.txt"), str(tmp_path / "o.txt")
    fieldlog.event("still written", p, old)
    assert "still written" in open(p).read()


@pytest.mark.parametrize("kind,msg,level", [
    # Real lines from the 2026-11-10..14 field log.
    ("event", "BOOT cause=PWRON_RESET last_phase=- free=44096 heap=144768 idf=34028/32768", "info"),
    ("event", "BOOT cause=HARD_RESET last_phase=selfheal free=41568 heap=144768 idf=34020/32768", "info"),
    ("event", "BOOT cause=HARD_RESET last_phase=fetch free=41696 heap=144768 idf=33996/32768", "warn"),
    ("event", "BOOT cause=WDT_RESET last_phase=fetch free=42240 heap=144768 idf=33984/32768", "error"),
    ("event", "SELF-HEAL RESET #1: no successful fetch for 20 min (free=24096 heap=143168 idf=4060/3200)", "error"),
    ("event", "FATAL (reset in 300 s): OSError('out of memory',)", "error"),
    ("event", "unexpected fetch error: OSError()", "error"),
    ("event", "stop 'Brunn' DOWN: OSError: HTTP 429 (free=21312 heap=143040 idf=5244/4096)", "warn"),
    ("event", "stop 'Brunn' UP after 2m01s, 2 failed pulls", "info"),
    ("event", "wifi: forced reconnect -> FAILED (repeats counted in hb) heap=143168 idf=5216/3200", "warn"),
    ("event", "wifi: forced reconnect raised: OSError(...)", "warn"),
    ("event", "ntp: resync FAILED: -203", "warn"),
    ("event", "ntp: resync ok again", "info"),
    ("event", "weather: ERROR shown (no fresh reading)", "warn"),
    ("event", "weather: ok again", "info"),
    ("event", "state restored: departures=[4, 8] frame=True heal=1", "info"),
    ("event", "hb up=5h13m free=23400 heap=140104 idf=416/136 pulls=60 fail=5 blips=2", "info"),
    ("event", "logship: 5 lines dropped (queue full)", "warn"),
    ("tick", "pull=ok,ok free=18400 heap=143168 idf=4152/3200 rssi=-69 up=0m", "info"),
    ("tick", "pull=ok,FAIL:HTTP 429 free=18400 heap=143168 idf=4152/3200 rssi=-69 up=0m", "warn"),
    ("render", "render partial for 11:31 prep=9644 wave=464 lands=+120ms | Rosenmalm | 16 min", "debug"),
])
def test_level_for(kind, msg, level):
    assert logship.level_for(kind, msg) == level


def test_rfc3339_unset_clock_is_none():
    assert logship.rfc3339((2000, 1, 1, 0, 0, 5, 5, 1)) is None


def test_event_with_buffered_time_and_tick_render_kinds():
    logship.configure("http://h:1")
    logship.event("early line", (2026, 11, 14, 10, 0, 0, 5, 318))
    logship.tick("pull=ok,ok")
    logship.render("render partial for 11:00 | x")
    k = [(e[0], e[2] is not None, e[3] is not None) for e in logship._q]
    assert logship._q[0][2] == "2026-11-14T10:00:00Z"
    assert k[1] == ("tick", False, True)      # server-stamped, device time as dev_time
    assert k[2] == ("render", True, False)    # device-stamped
