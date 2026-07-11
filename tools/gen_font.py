"""Host-side generator: render a TrueType face into a compact 1-bit
binary bitmap font (`.fnt`) that the device streams glyph-by-glyph from
flash (see src/bitfont.py). Run on the host with Pillow; never on the
device.

Why a custom binary format instead of peterhinch/font_to_py: font_to_py
emits a Python *module* whose glyph data stays RESIDENT in RAM, and even
a ~15KB resident font reliably crashed this board's live fetch/render
loop while allocating the 48KB framebuffer (no PSRAM, fragmented heap --
see CLAUDE.md "RAM-vs-HTTPS conflict", confirmed on hardware 2026-07-11).
This format instead keeps the whole font on flash (4MB, cheap) and the
device reads one glyph at a time (resident cost ~= one glyph). That is
the entire reason a smoother font is viable here at all.

The panel is 1-bit (no anti-aliasing), so "smooth" comes purely from
rendering each glyph at its real target pixel size -- real curves --
rather than scaling an 8x8 cell 10x into blocks. Glyphs are thresholded
to pure black/white here so what we store is exactly what the panel
shows.

Format `.fnt` v1 (little-endian), designed for zero-resident random
access -- the device binary-searches the on-disk index, it never loads a
glyph table into RAM:

    header:  <4s B B H>  magic=b"BFN1", height, baseline, count
    index:   count x <H B B I>  code, width, advance, offset
             (sorted ascending by code, so the device can binary-search)
    bitmaps: per glyph, `height` rows x ceil(width/8) bytes, MSB-first,
             bit 1 = black. Glyph byte length = ceil(width/8) * height.

Each glyph is rendered into an advance-width cell (width == advance) with
the glyph at its natural left bearing on a shared baseline, so laying
cells edge-to-edge reproduces normal (un-kerned) text spacing with no
per-glyph bearing metadata -- validated in tools/gen_font_check.py.
"""
import argparse
import struct

from PIL import Image, ImageDraw, ImageFont

MAGIC = b"BFN1"
HDR = struct.Struct("<4sBBH")
IDX = struct.Struct("<HBBI")

# Printable ASCII. departures._to_ascii transliterates Swedish a/a/o/e/u
# to plain ASCII upstream, so this covers every character the screen can
# draw. Space (0x20) included: it has no ink but a real advance. Plus the
# degree sign (U+00B0), needed for the weather footer's temperatures --
# outside ASCII, so it's appended explicitly here (and to weather.py's
# format strings).
DEFAULT_CHARSET = "".join(chr(c) for c in range(0x20, 0x7F)) + "°"

THRESHOLD = 128  # grayscale coverage >= this becomes a black pixel


def render_rows(font, ch, ascent, descent):
    """(advance_px, set-pixel rows) for one char in an advance-wide,
    (ascent+descent)-tall cell, glyph on the shared baseline at row
    `ascent`. anchor="ls" = x is the pen origin (left bearing preserved),
    y is the baseline -- so cells laid edge-to-edge space like normal
    text. Returns rows as a list (len == ascent+descent) of sets of set-x
    columns, so build() can find the global ink extent and crop."""
    adv = max(1, round(font.getlength(ch)))
    height = ascent + descent
    img = Image.new("L", (adv, height), 0)
    ImageDraw.Draw(img).text((0, ascent), ch, font=font, fill=255, anchor="ls")
    px = img.load()
    rows = [set(x for x in range(adv) if px[x, y] >= THRESHOLD) for y in range(height)]
    return adv, rows


def build(ttf_path, size, weight, out_path, charset=DEFAULT_CHARSET):
    font = ImageFont.truetype(ttf_path, size)
    if weight is not None:
        try:
            font.set_variation_by_axes([weight])
        except Exception as e:
            raise SystemExit("could not set weight axis %s: %s" % (weight, e))
    ascent, descent = font.getmetrics()
    full_h = ascent + descent

    rendered = [(ord(ch), *render_rows(font, ch, ascent, descent)) for ch in charset]

    # Crop uniformly to the union ink box across the whole charset: the
    # font's line box carries generous leading/descent that's dead space
    # for a glance display (digits have no descenders at all). Trimming
    # the same top/bottom off every glyph keeps all baselines aligned
    # while making the hero cell ~= real digit height -> compact vertical
    # layout, less flash. A shared baseline is what lets display.py mix
    # sizes on one line.
    inked = [y for _, _, rows in rendered for y in range(full_h) if rows[y]]
    top, bottom = min(inked), max(inked) + 1  # bottom exclusive
    height = bottom - top
    baseline = ascent - top

    glyphs = []  # (code, advance, bitmap bytes)
    for code, adv, rows in rendered:
        row_bytes = (adv + 7) >> 3
        data = bytearray(row_bytes * height)
        for i, y in enumerate(range(top, bottom)):
            for x in rows[y]:
                data[i * row_bytes + (x >> 3)] |= 0x80 >> (x & 7)
        glyphs.append((code, adv, bytes(data)))
    glyphs.sort()  # by code -- device binary-searches the index

    index_size = IDX.size * len(glyphs)
    data_start = HDR.size + index_size
    out = bytearray()
    out += HDR.pack(MAGIC, height, baseline, len(glyphs))
    offset = data_start
    body = bytearray()
    for code, adv, data in glyphs:
        out += IDX.pack(code, adv, adv, offset)  # width == advance (advance-width cells)
        body += data
        offset += len(data)
    out += body

    with open(out_path, "wb") as f:
        f.write(out)
    print("wrote %s: %d glyphs, height=%d baseline=%d, %d bytes (%.1f KB)" % (
        out_path, len(glyphs), height, baseline, len(out), len(out) / 1024))
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ttf")
    ap.add_argument("out")
    ap.add_argument("--size", type=int, required=True, help="pixel size passed to truetype()")
    ap.add_argument("--weight", type=float, default=None, help="variable-font wght axis (e.g. 700)")
    ap.add_argument("--charset", default=DEFAULT_CHARSET)
    args = ap.parse_args()
    build(args.ttf, args.size, args.weight, args.out, args.charset)


if __name__ == "__main__":
    main()
