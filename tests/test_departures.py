"""Host-side tests for the pure parse/filter/format logic in departures.py.
Fixtures below are trimmed real shapes from SL's departures endpoint
(GET /v1/sites/{siteId}/departures) -- see CLAUDE.md "SL Transport API".
"""
import departures


def _dep(designation, destination, display, scheduled, expected=None):
    return {
        "destination": destination,
        "display": display,
        "scheduled": scheduled,
        "expected": expected if expected is not None else scheduled,
        "line": {"designation": designation},
    }


SAMPLE_RAW = {
    "departures": [
        _dep("430X", "Slussen", "14:12", "2026-07-09T14:12:58"),
        _dep("430X", "Eknäs", "Nu", "2026-07-09T13:21:41"),
        _dep("429X", "Idalen", "13:52", "2026-07-09T13:52:53"),
    ]
}

EMPTY_RAW = {"departures": []}


def test_parse_departures_sorts_by_expected_time():
    deps = departures.parse_departures(SAMPLE_RAW)
    assert [d["destination"] for d in deps] == ["Eknas", "Idalen", "Slussen"]


def test_parse_departures_sorts_by_expected_not_scheduled_when_delayed():
    # Bus A is scheduled earlier (13:00) but running late (expected 13:30,
    # shown as a clock time since it's now far out); bus B is scheduled
    # later (13:05) but on time (expected 13:06, shown as "1 min"). A rider
    # sees B first -- sorting by `scheduled` would wrongly put A first.
    raw = {
        "departures": [
            _dep("1", "A-dest", "13:30", scheduled="2026-07-09T13:00:00", expected="2026-07-09T13:30:00"),
            _dep("2", "B-dest", "1 min", scheduled="2026-07-09T13:05:00", expected="2026-07-09T13:06:00"),
        ]
    }
    deps = departures.parse_departures(raw)
    assert [d["destination"] for d in deps] == ["B-dest", "A-dest"]


def test_parse_departures_extracts_fields():
    deps = departures.parse_departures(SAMPLE_RAW)
    first = deps[0]
    assert first["line"] == "430X"
    assert first["destination"] == "Eknas"
    assert first["display"] == "Nu"


def test_parse_departures_handles_empty_or_falsy():
    assert departures.parse_departures(EMPTY_RAW) == []
    assert departures.parse_departures(None) == []
    assert departures.parse_departures({}) == []


def test_parse_departures_transliterates_swedish_characters():
    raw = {"departures": [_dep("430X", "Eknäs", "5 min", "2026-07-09T13:30:00")]}
    deps = departures.parse_departures(raw)
    assert deps[0]["destination"] == "Eknas"


def test_split_hero_display_minutes():
    assert departures.split_hero_display("5 min") == ("5", "min")
    assert departures.split_hero_display("12 min") == ("12", "min")


def test_split_hero_display_now():
    assert departures.split_hero_display("Nu") == ("Nu", None)


def test_split_hero_display_clock_time():
    assert departures.split_hero_display("12:34") == ("12:34", None)


# --- local countdowns ---------------------------------------------------------
# Expected rules verified against the live SL API 2026-11-08: floor minutes,
# "Nu" < 1 min, "N min" < 30 min, else the expected clock time.

NOW = (2026, 11, 8, 12, 19, 0)


def _cached(expected, display="?", line="55", dest="Finnberget"):
    return {"line": line, "destination": dest, "display": display, "expected": expected}


def _shown(expected):
    out = departures.with_local_countdowns([_cached(expected)], NOW)
    return out[0]["display"] if out else None


def test_local_countdown_matches_sl_rules():
    assert _shown("2026-11-08T12:19:40") == "Nu"
    assert _shown("2026-11-08T12:20:00") == "1 min"
    assert _shown("2026-11-08T12:21:16") == "2 min"      # 136 s -> floor 2
    assert _shown("2026-11-08T12:48:59") == "29 min"
    assert _shown("2026-11-08T12:49:00") == "12:49"      # 30 min -> clock time
    assert _shown("2026-11-08T12:54:31") == "12:54"


def test_local_countdown_keeps_just_departed_then_drops():
    assert _shown("2026-11-08T12:18:30") == "Nu"         # 30 s past: still at stop
    assert _shown("2026-11-08T12:17:59") is None         # >60 s past: gone


def test_local_countdown_crosses_midnight():
    out = departures.with_local_countdowns([_cached("2026-11-09T00:05:00")], (2026, 11, 8, 23, 58, 0))
    assert out[0]["display"] == "7 min"


def test_local_countdown_does_not_mutate_cache_and_keeps_order():
    cache = [_cached("2026-11-08T12:15:00", "Nu"), _cached("2026-11-08T12:25:00", "9 min")]
    out = departures.with_local_countdowns(cache, NOW)
    assert [d["display"] for d in out] == ["6 min"]
    assert cache[1]["display"] == "9 min"


def test_local_countdown_unparseable_expected_keeps_sl_display():
    out = departures.with_local_countdowns([_cached("garbage", "5 min")], NOW)
    assert out[0]["display"] == "5 min"


def test_outage_rolls_cached_departures_forward():
    # Fetched at 12:19, then every pull fails. Ten minutes later the first two
    # buses have gone and the third has rolled up -- no frozen "1 min".
    cache = [_cached("2026-11-08T12:20:00"), _cached("2026-11-08T12:24:00"), _cached("2026-11-08T12:35:00")]
    out = departures.with_local_countdowns(cache, (2026, 11, 8, 12, 29, 0))
    assert [d["display"] for d in out] == ["6 min"]


# --- RTC-memory cache encode/decode -------------------------------------------

def test_cache_roundtrip():
    caches = [[_cached("2026-11-08T12:20:00", "1 min")], []]
    extra = {"frame": None, "full_t": 123, "heal": 2}
    blob = departures.encode_cache([1, 2], caches, [100, None], extra)
    got_caches, got_times, got_extra = departures.decode_cache(blob, [1, 2])
    assert got_times == [100, None] and got_extra == extra
    # `display` isn't stored (recomputed each render); decode falls back to the clock time
    assert got_caches == [[_cached("2026-11-08T12:20:00", "12:20")], []]


def test_cache_rejected_for_different_stops_or_garbage():
    blob = departures.encode_cache([1, 2], [[], []], [None, None])
    assert departures.decode_cache(blob, [1, 3]) is None
    assert departures.decode_cache(b"", [1]) is None
    assert departures.decode_cache(b"\xff{not json", [1]) is None


def test_cache_trimmed_to_fit():
    big = [[_cached("2026-11-08T12:%02d:00" % m, "x", dest="A long destination name") for m in range(20)]] * 2
    blob = departures.encode_cache([1, 2], big, [1, 1], max_bytes=600)
    assert len(blob) <= 600
    caches, _, _ = departures.decode_cache(blob, [1, 2])
    assert caches[0] and caches[0][0]["expected"] == "2026-11-08T12:00:00"  # soonest kept


# --- streaming splitter (fetch RAM fix) --------------------------------------
import json
import os
import random

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sl_slussen.json")


def _stream_parse(raw_bytes, chunk_sizes):
    sp = departures.DepartureSplitter()
    deps, i = [], 0
    for n in chunk_sizes:
        for obj in sp.feed(raw_bytes[i:i + n]):
            deps.append(departures.compact_departure(json.loads(obj)))
        i += n
        if i >= len(raw_bytes):
            break
    return sp, deps


def test_splitter_matches_full_parse_on_real_sl_reply_any_chunking():
    raw = open(_FIXTURE, "rb").read()
    full = [departures.compact_departure(d) for d in json.loads(raw)["departures"]]
    assert len(full) > 10
    rng = random.Random(1)
    for _ in range(20):
        sizes = [rng.randint(1, 700) for _ in range(len(raw))]
        sp, got = _stream_parse(raw, sizes)
        assert sp.found_key and got == full
    # and the result feeds parse_departures exactly like the full reply did
    assert departures.parse_departures({"departures": got}) == departures.parse_departures(json.loads(raw))


def test_splitter_handles_escapes_braces_in_strings_and_unicode():
    tricky = {"departures": [
        {"destination": 'Q"uote {not} [a] \ brace', "display": "Nu", "expected": "2026-11-08T12:00:00",
         "line": {"designation": "1"}, "deviations": [{"text": "}]{["}]},
        {"destination": "Eknäs", "display": "5 min", "expected": "2026-11-08T12:05:00",
         "line": {"designation": "2"}}],
        "stop_deviations": [{"departures": "decoy"}]}
    raw = json.dumps(tricky, ensure_ascii=False).encode("utf-8")
    for n in (1, 2, 3, 7, 1000):
        sp, got = _stream_parse(raw, [n] * len(raw))
        assert [d["destination"] for d in got] == ['Q"uote {not} [a] \ brace', "Eknäs"]


def test_splitter_reports_missing_departures_key():
    sp, got = _stream_parse(b'{"message": "rate limited"}', [5] * 40)
    assert not sp.found_key and got == []
    sp, got = _stream_parse(b'{"departures": [], "stop_deviations": []}', [3] * 40)
    assert sp.found_key and got == []
