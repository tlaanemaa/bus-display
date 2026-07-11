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
