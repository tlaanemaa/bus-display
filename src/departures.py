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


# --- Local countdowns (added 2026-11-08) -------------------------------------
# SL's `display` string is a snapshot of "how long from the moment SL answered".
# Cached across a failed pull, it freezes ("5 min" for ten minutes) and never
# drops buses that have left. So the countdown is recomputed every render from
# the ABSOLUTE `expected` time against the device's own NTP-synced clock --
# fresh data and cached data are treated identically, and an outage degrades
# gracefully (countdowns keep ticking, departed buses fall off) instead of
# freezing. The rules below match SL's own `display` (checked against the live
# API 2026-11-08): floor to whole minutes, "Nu" under 1 min, "N min" under
# 30 min, else the expected clock time "HH:MM".

CLOCK_TIME_AFTER_MIN = 30   # SL switches from "N min" to "HH:MM" at 30 min
KEEP_PAST_S = 60            # keep a bus as "Nu" up to this long past `expected`


def parse_iso_local(s):
    """'2026-11-08T12:21:16' -> (y, mo, d, h, mi, s). Ignores any trailing
    fraction/offset (SL sends neither, but be lenient)."""
    return (int(s[0:4]), int(s[5:7]), int(s[8:10]),
            int(s[11:13]), int(s[14:16]), int(s[17:19]) if len(s) >= 19 else 0)


def format_countdown(secs, expected_iso):
    """SL-style display string for a departure `secs` seconds from now."""
    if secs < 60:
        return "Nu"
    if secs < CLOCK_TIME_AFTER_MIN * 60:
        return "%d min" % (secs // 60)
    return expected_iso[11:16]


def with_local_countdowns(deps, now_local):
    """Recompute every departure's `display` from its `expected` time.

    deps: parse_departures() output (each has `expected`, local ISO time).
    now_local: (y, mo, d, h, mi, s) -- the device's current Stockholm time.
    Returns NEW dicts (inputs untouched -- they're the cache), dropping
    departures more than KEEP_PAST_S past their expected time. A departure
    whose `expected` won't parse keeps SL's own display rather than vanishing.
    """
    import localtime
    now_s = localtime.naive_seconds(*now_local)
    out = []
    for d in deps:
        try:
            secs = localtime.naive_seconds(*parse_iso_local(d["expected"])) - now_s
        except (ValueError, TypeError, KeyError, IndexError):
            out.append(d)
            continue
        if secs < -KEEP_PAST_S:
            continue
        nd = dict(d)
        nd["display"] = format_countdown(secs, d["expected"])
        out.append(nd)
    return out


# --- State persistence across resets (pure encode/decode) --------------------
# main.py keeps a small state blob in RTC memory (survives machine.reset() and
# watchdog resets, NOT power loss; RAM, so no flash wear; ~2 KB): the
# departures cache, so a reboot doesn't blank the screen, plus `extra` -- what
# is physically on the panel (so a reboot needs no full flashing refresh) and
# the self-heal backoff counter. These two functions are the pure, testable
# halves. Cache rows drop `display` (recomputed from `expected` every render).

def encode_cache(site_ids, caches, fetch_times, extra=None, max_bytes=2000):
    """JSON bytes for the state, trimmed (latest departures dropped first)
    until it fits max_bytes. Returns None if even an empty cache won't fit."""
    import json
    caches = [list(c) for c in caches]
    while True:
        payload = {
            "ids": list(site_ids),
            "t": list(fetch_times),
            "c": [[[d["line"], d["destination"], d["expected"]] for d in c] for c in caches],
            "x": extra,
        }
        blob = json.dumps(payload).encode()
        if len(blob) <= max_bytes:
            return blob
        longest = max(range(len(caches)), key=lambda i: len(caches[i])) if caches else None
        if longest is None or not caches[longest]:
            return None
        caches[longest].pop()


def decode_cache(blob, site_ids):
    """Inverse of encode_cache. Returns (caches, fetch_times, extra), or None
    if the blob is missing/corrupt or was saved for a different stop list."""
    import json
    if not blob:
        return None
    try:
        p = json.loads(blob)
        if list(p["ids"]) != list(site_ids):
            return None
        caches = [[{"line": r[0], "destination": r[1], "expected": r[2],
                    "display": r[2][11:16]} for r in c]   # SL-style clock-time fallback
                  for c in p["c"]]
        times = list(p["t"])
        if len(caches) != len(site_ids) or len(times) != len(site_ids):
            return None
        return caches, times, p.get("x")
    except Exception:
        return None


# --- Streaming extraction (added 2026-11-08) ---------------------------------
# json.loads() of a whole SL reply builds every departure's full nested object
# tree at once: at forecast_min=180 that peak grew the MicroPython heap ~25 KB,
# and on this board the heap grows by taking memory from the ESP-IDF pool the
# Wi-Fi/lwIP stack needs -- permanently. After one fetch DNS failed (-202) on
# every later request (measured on hardware). So sl.py streams the reply
# through this splitter: it yields each element of the top-level "departures"
# array as its own small JSON bytes, which are parsed and reduced to the four
# fields we use before the next one is read. Peak = one departure (<1 KB).
#
# Works on BYTES: JSON's structural characters are ASCII and UTF-8 multibyte
# sequences never contain ASCII bytes, so splitting mid-character across chunk
# boundaries is harmless.

_QUOTE, _BSLASH = 0x22, 0x5C
_LBRACE, _RBRACE, _LBRACK, _RBRACK = 0x7B, 0x7D, 0x5B, 0x5D


class DepartureSplitter:
    """Feed raw reply chunks with feed(); it returns the list of complete
    departure objects (bytes) found so far. `found_key` tells whether the
    "departures" array was seen at all (no key = not a departures reply)."""

    def __init__(self):
        self.depth = 0          # JSON nesting depth (outside strings)
        self.in_str = False
        self.esc = False
        self.key = bytearray()  # current depth-1 string (candidate key)
        self.last_key = None
        self.in_array = False   # inside the top-level "departures" array
        self.buf = None         # bytes of the departure object being captured
        self.found_key = False

    def feed(self, chunk):
        out = []
        for b in chunk:
            cap = self.buf is not None
            if cap:
                self.buf.append(b)
            if self.in_str:
                if self.esc:
                    self.esc = False
                elif b == _BSLASH:
                    self.esc = True
                elif b == _QUOTE:
                    self.in_str = False
                    if self.depth == 1 and not cap:
                        self.last_key = bytes(self.key)
                elif self.depth == 1 and not cap:
                    self.key.append(b)
                continue
            if b == _QUOTE:
                self.in_str = True
                if self.depth == 1 and not cap:
                    self.key = bytearray()
            elif b == _LBRACE or b == _LBRACK:
                self.depth += 1
                if b == _LBRACK and self.depth == 2 and self.last_key == b"departures":
                    self.in_array = True
                    self.found_key = True
                elif b == _LBRACE and self.in_array and self.depth == 3 and not cap:
                    self.buf = bytearray(b"{")
            elif b == _RBRACE or b == _RBRACK:
                self.depth -= 1
                if cap and self.depth == 2:
                    out.append(bytes(self.buf))
                    self.buf = None
                elif b == _RBRACK and self.in_array and self.depth == 1:
                    self.in_array = False
        return out


def compact_departure(d):
    """Keep only what parse_departures() reads, in the same shape."""
    return {"destination": d["destination"], "display": d["display"],
            "expected": d["expected"], "line": {"designation": d["line"]["designation"]}}
