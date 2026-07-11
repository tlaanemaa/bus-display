"""Streamed 1-bit bitmap font reader. Renders smooth, print-like text on
the e-paper panel WITHOUT keeping the font resident in RAM -- the whole
reason a nicer font is viable on this board at all.

Background (CLAUDE.md "RAM-vs-HTTPS conflict"): peterhinch/font_to_py
emits a Python module whose glyph data stays resident, and even a ~15KB
resident font reliably crashed the live fetch/render loop allocating the
48KB framebuffer (no PSRAM, fragmented heap; confirmed on hardware
2026-07-11). This module instead keeps the font on flash (4MB, cheap)
and reads ONE glyph at a time.

TWO RAM disciplines are load-bearing here, both learned the hard way on
this PSRAM-less board where mbedtls's RSA-2048 SL handshake needs a large
*contiguous* block that fragments away easily (CLAUDE.md "RAM-vs-HTTPS
conflict"):

  1. Never hold a font file open across a fetch -- each measure()/draw()
     opens and closes it. During the fetch, zero font files are open.

  2. The draw path must not churn the heap. Every allocation made while
     the 48KB framebuffer is alive can strand an object into the region
     the next TLS handshake needs. So: one module-level scratch buffer
     (`_GBUF`), pre-sized big enough for the largest glyph and reused via
     readinto (no per-glyph bytes); callers pass a module-level plot
     function + the fb (no per-call lambda closures). Warm the advance
     caches once at boot (see warm() / display.warm_fonts) so nothing new
     is allocated during a draw. Deployed as bitfont.mpy so importing it
     doesn't compile-fragment the heap either (CLAUDE.md gotchas).

Pure enough to run under host pytest: only imports `struct`, opens a
file, and draws through a caller-supplied `plot` callback -- no
`framebuf`/`machine`/`network`. See tools/gen_font.py for the `.fnt`
binary format; this reader is its exact counterpart.
"""
import struct

_HDR = "<4sBBH"          # magic, height, baseline, count
_HDR_SIZE = struct.calcsize(_HDR)
_IDX = "<HBBI"           # code, width, advance, offset
_IDX_SIZE = struct.calcsize(_IDX)
_MAGIC = b"BFN1"

# One shared glyph-bitmap scratch, reused by every draw of every font.
# Pre-sized (at import, on a clean boot heap) larger than the biggest
# glyph any font here produces (hero digit ~= 10 row-bytes x 87 rows
# ~= 870 B), so it NEVER grows mid-draw -- a mid-draw grow would strand a
# buffer into the framebuffer region and starve the next TLS handshake.
_GBUF = bytearray(1200)
_IDXBUF = bytearray(_IDX_SIZE)  # reused for index-entry reads (no per-lookup bytes)


class Font:
    """One `.fnt` file, addressed by path (NOT held open -- see module
    docstring). `height` is the (cropped) cell height; `baseline` is rows
    from the cell top to the baseline -- shared across a face's glyphs,
    so callers can align differently-sized fonts on one line by matching
    baselines (see display.py's hero + unit)."""

    def __init__(self, path):
        self.path = path
        f = open(path, "rb")
        try:
            magic, self.height, self.baseline, self._count = struct.unpack(
                _HDR, f.read(_HDR_SIZE))
        finally:
            f.close()
        if magic != _MAGIC:
            raise ValueError("bad font magic in %s" % path)
        self._adv = {}  # code -> advance px (cheap; bitmaps never cached)

    def _entry(self, f, code):
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

    def warm(self, charset):
        """Populate the advance cache for every char in `charset`, once,
        at boot -- so later measure() calls on a live heap allocate
        nothing (see module docstring point 2)."""
        f = open(self.path, "rb")
        try:
            for ch in charset:
                code = ord(ch)
                if code not in self._adv:
                    e = self._entry(f, code)
                    self._adv[code] = e[1] if e else 0
        finally:
            f.close()

    def measure(self, s, tracking=0):
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

    def draw(self, s, x, y, color, fb, plot, tracking=0):
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
