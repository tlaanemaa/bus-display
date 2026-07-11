"""Validate the .fnt advance-width-cell model: generate a font, then
rasterize a test string by laying glyph cells edge-to-edge (exactly what
src/bitfont.py does on-device) and compare, pixel-for-pixel in width and
visually, against Pillow rendering the same string in one call. If the
per-glyph model is right, spacing matches. Host-only sanity check."""
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_font

HDR = gen_font.HDR
IDX = gen_font.IDX


def load(path):
    with open(path, "rb") as f:
        blob = f.read()
    magic, height, baseline, count = HDR.unpack_from(blob, 0)
    assert magic == gen_font.MAGIC
    idx = {}
    for i in range(count):
        code, w, adv, off = IDX.unpack_from(blob, HDR.size + i * IDX.size)
        idx[code] = (w, adv, off)
    return blob, height, baseline, idx


def rasterize(blob, height, idx, s):
    """Lay glyph cells edge-to-edge into a 1-bit-ish grayscale image."""
    total = sum(idx[ord(c)][1] for c in s if ord(c) in idx)
    img = Image.new("L", (max(1, total), height), 0)
    px = img.load()
    penx = 0
    for c in s:
        g = idx.get(ord(c))
        if not g:
            continue
        w, adv, off = g
        row_bytes = (w + 7) >> 3
        for y in range(height):
            base = off + y * row_bytes
            for x in range(w):
                if blob[base + (x >> 3)] & (0x80 >> (x & 7)):
                    px[penx + x, y] = 255
        penx += adv
    return img, total


def main():
    ttf = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "fonts", "Bitter-var.ttf")
    outdir = os.path.dirname(os.path.abspath(__file__))
    fnt = os.path.join(outdir, "_check.fnt")
    gen_font.build(ttf, 31, 700, fnt, gen_font.DEFAULT_CHARSET)
    blob, height, baseline, idx = load(fnt)

    s = "402  Nacka forum  12 min  Gustavsberg 14:43"
    mine, total = rasterize(blob, height, idx, s)

    # Pillow direct, same face/size/weight, for reference spacing.
    font = ImageFont.truetype(ttf, 31)
    font.set_variation_by_axes([700])
    ref_w = round(font.getlength(s))
    ref = Image.new("L", (max(1, ref_w), height), 0)
    ImageDraw.Draw(ref).text((0, baseline), s, font=font, fill=255, anchor="ls")

    print("streamed cells total width:", total)
    print("pillow direct getlength   :", ref_w)
    print("width delta (px)          :", total - ref_w)

    # Stack for eyeball compare: mine on top, pillow below.
    combo = Image.new("L", (max(mine.width, ref.width), height * 2 + 4), 40)
    combo.paste(mine, (0, 0))
    combo.paste(ref, (0, height + 4))
    combo.point(lambda p: 0 if p < 128 else 255).save(os.path.join(outdir, "_check.png"))
    print("saved", os.path.join(outdir, "_check.png"), "(top=streamed cells, bottom=pillow direct)")
    os.remove(fnt)


if __name__ == "__main__":
    main()
