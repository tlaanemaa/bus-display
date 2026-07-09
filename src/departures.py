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
    as corrupted glyphs (confirmed on real hardware for "Eknäs").
    Transliterate to plain ASCII until custom fonts are generated (see
    CLAUDE.md "Key library choices" -- declared, not yet built)."""
    for accented, plain in _ASCII_FALLBACK:
        s = s.replace(accented, plain)
    return s


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


def format_line(dep, line_w=4, dest_w=14, disp_w=6):
    """One fixed-width display row: "<line> <destination>  <display>".
    Column widths are parameters so callers can request a narrower
    version to fit a bigger font scale (see display.py)."""
    fmt = "%%-%ds %%-%ds %%%ds" % (line_w, dest_w, disp_w)
    return fmt % (dep["line"][:line_w], dep["destination"][:dest_w], dep["display"][:disp_w])


def format_caption(dep, line_w=4, dest_w=20):
    """"<line>  <destination>" with no trailing `display` field (shown
    separately, huge, by the caller -- see display.py's hero treatment)
    and deliberately no fixed-width padding: this gets centered on
    screen, and trailing padding spaces would throw off that centering
    math."""
    return "%s  %s" % (dep["line"][:line_w], dep["destination"][:dest_w])
