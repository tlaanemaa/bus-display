"""Streamed 1-bit Bitter font reader for the e-paper panel.

Glyph data remains on flash and this module reads one glyph at a time, which
keeps the single 48 KB framebuffer affordable on the PSRAM-less ESP32. A
resident glyph module is not acceptable here.

The draw path reuses module-level scratch storage. `warm()` is available to
prepopulate advance widths when a caller needs it, while normal measurement
fills missing widths on demand. Font files are opened only for the current
measure/draw operation and then closed. The module ships as bytecode so the
device does not compile it during boot.

This is host-testable: it imports only `struct`, opens files, and draws through
a supplied `plot` callback. `tools/gen_font.py` defines the matching `.fnt`
format.
"""
import struct

if False:
    from typing import Any, Callable

_HDR = "<4sBBH"          # magic, height, baseline, count
_HDR_SIZE = struct.calcsize(_HDR)
_IDX = "<HBBI"           # code, width, advance, offset
_IDX_SIZE = struct.calcsize(_IDX)
_MAGIC = b"BFN1"

# One shared glyph-bitmap scratch, reused by every draw of every font.
# Pre-sized (at import, on a clean boot heap) larger than the biggest
# glyph any font here produces (hero digit ~= 10 row-bytes x 87 rows
# ~= 870 B), so it never grows during a rendered frame.
_GBUF = bytearray(1200)
_IDXBUF = bytearray(_IDX_SIZE)  # reused for index-entry reads (no per-lookup bytes)


class Font:
    """One `.fnt` file, addressed by path (NOT held open -- see module
    docstring). `height` is the (cropped) cell height; `baseline` is rows
    from the cell top to the baseline -- shared across a face's glyphs,
    so callers can align differently-sized fonts on one line by matching
    baselines (see display.py's hero + unit)."""

    def __init__(self, path: str) -> None:
        self.path = path
        f = open(path, "rb")
        try:
            magic, self.height, self.baseline, self._count = struct.unpack(
                _HDR, f.read(_HDR_SIZE))
        finally:
            f.close()
        if magic != _MAGIC:
            raise ValueError("bad font magic in %s" % path)
        self._adv = {}  # type: dict[int, int]

    def _entry(
        self, f: "Any", code: int,
    ) -> "tuple[int, int, int] | None":
        """(width, advance, offset) for a codepoint, or None. Binary-
        search the on-disk index (sorted by code) via seeks on the
        already-open file `f`, reading each 8-byte entry into the shared
        _IDXBUF -- the glyph table is never loaded into RAM and the search
        allocates nothing."""
        lo, hi = 0, self._count - 1
        while lo <= hi:
            mid = (lo + hi) >> 1
            f.seek(_HDR_SIZE + mid * _IDX_SIZE)
            f.readinto(_IDXBUF)
            c, w, adv, off = struct.unpack(_IDX, _IDXBUF)
            if c == code:
                return w, adv, off
            if c < code:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def warm(self, charset: str) -> None:
        """Populate the advance cache for every char in `charset`."""
        f = open(self.path, "rb")
        try:
            for ch in charset:
                code = ord(ch)
                if code not in self._adv:
                    e = self._entry(f, code)
                    self._adv[code] = e[1] if e else 0
        finally:
            f.close()

    def measure(self, s: str, tracking: int = 0) -> int:
        """Total advance width of `s` in px, matching what draw() lays
        down (same tracking rule). Uses the advance cache; opens the file
        only if some char hasn't been seen yet (shouldn't happen after
        warm())."""
        adv = self._adv
        missing = None
        for ch in s:
            if ord(ch) not in adv:
                if missing is None:
                    missing = []
                missing.append(ch)
        if missing:
            f = open(self.path, "rb")
            try:
                for ch in missing:
                    e = self._entry(f, ord(ch))
                    adv[ord(ch)] = e[1] if e else 0
            finally:
                f.close()
        w = 0
        for ch in s:
            w += adv[ord(ch)]
        if tracking and len(s) > 1:
            w += tracking * (len(s) - 1)
        return w

    def draw(
        self,
        s: str,
        x: int,
        y: int,
        color: int,
        fb: "Any",
        plot: "Callable[[Any, int, int, int, int], None]",
        tracking: int = 0,
    ) -> int:
        """Draw `s` with the top-left of its cell box at logical (x, y).
        `plot(fb, lx, ly, length, color)` fills one horizontal run -- a
        MODULE-LEVEL function (no per-call closure) that maps the run onto
        the physical panel through the 90deg rotation (framebuf can't draw
        rotated glyphs). Opens the font file only for this call; streams
        each glyph into the shared _GBUF via readinto (no per-glyph
        allocation). Returns the advance width laid down."""
        h = self.height
        penx = x
        buf = _GBUF
        mv = memoryview(buf)
        f = open(self.path, "rb")
        try:
            for ch in s:
                e = self._entry(f, ord(ch))
                if not e:
                    continue
                w, adv, off = e
                self._adv[ord(ch)] = adv
                row_bytes = (w + 7) >> 3
                nbytes = row_bytes * h
                if nbytes > len(_GBUF):
                    raise ValueError(
                        "glyph bitmap exceeds scratch buffer: %d > %d" % (nbytes, len(_GBUF))
                    )
                f.seek(off)
                f.readinto(mv[:nbytes])
                for gy in range(h):
                    base = gy * row_bytes
                    ly = y + gy
                    gx = 0
                    while gx < w:
                        if (gx & 7) == 0 and buf[base + (gx >> 3)] == 0:
                            gx += 8  # skip a fully-blank byte at a stride
                            continue
                        if not (buf[base + (gx >> 3)] & (0x80 >> (gx & 7))):
                            gx += 1
                            continue
                        run = gx + 1
                        while run < w and (buf[base + (run >> 3)] & (0x80 >> (run & 7))):
                            run += 1
                        plot(fb, penx + gx, ly, run - gx, color)
                        gx = run
                penx += adv + tracking
        finally:
            f.close()
        return penx - x - (tracking if s else 0)
