"""Pure logic: parse/filter/format SL departures JSON. No hardware or
network imports -- runs under host CPython with pytest (see CLAUDE.md
"Testability rule"). sl.py does the fetching; this module never touches
the network.
"""


_ASCII_FALLBACK = (
    ("å", "a"), ("Å", "A"),
    ("ä", "a"), ("Ä", "A"),
    ("ö", "o"), ("Ö", "O"),
    ("é", "e"), ("É", "E"),
    ("ü", "u"), ("Ü", "U"),
)


def _to_ascii(s):
    """framebuf's built-in font is ASCII-only; SL destination/stop names
    routinely contain Swedish a/a/o with diacritics, which otherwise draw
    as corrupted glyphs (confirmed on real hardware for "Eknäs"). Custom
    fonts that would support these natively are RAM-unviable on this
    board (see CLAUDE.md "Key library choices" / "RAM-vs-HTTPS
    conflict") -- transliterating to plain ASCII is the permanent fix,
    not a stopgap."""
    for accented, plain in _ASCII_FALLBACK:
        s = s.replace(accented, plain)
    return s


def split_hero_display(display):
    """Split an SL `display` string into (main, unit) for the hero
    treatment: the big countdown number rendered huge, with any trailing
    unit word ("min") rendered smaller alongside it (see CLAUDE.md
    "Screen design"). Only splits on a trailing alphabetic word -- "5
    min" -> ("5", "min"), "Nu" -> ("Nu", None) (nothing to demote), "12:34"
    -> ("12:34", None) (a clock time has no unit)."""
    if " " in display:
        main, _, unit = display.partition(" ")
        if unit.isalpha():
            return main, unit
    return display, None


def parse_departures(raw_json):
    """raw_json: the dict returned by sl.fetch_departures() (or an
    equivalent fixture in tests) -- {"departures": [...]}. Returns a list
    of plain dicts sorted by `expected` time, or [] if raw_json is falsy
    or has no departures.

    Sorted by `expected`, NOT `scheduled` -- a delayed bus's `scheduled`
    time can be earlier than an on-time bus's, which used to put it first
    in the list even though it actually arrives later (confirmed on real
    hardware: a clock-time departure showing before an earlier relative-
    time one). `expected` is SL's live prediction and matches what
    `display` is computed from, so sorting by it keeps the list in the
    same order a rider would actually experience the buses arriving.
    """
    if not raw_json:
        return []
    deps = [
        {
            "line": _to_ascii(d["line"]["designation"]),
            "destination": _to_ascii(d["destination"]),
            "display": d["display"],
            "expected": d["expected"],
        }
        for d in raw_json.get("departures", [])
    ]
    deps.sort(key=lambda d: d["expected"])
    return deps
