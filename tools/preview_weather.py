"""Render the procedural weather glyphs on the host, exactly as the panel
would draw them (through the real 90deg mount transform), so their shape
and legibility can be judged before a deploy->reset->eyeball cycle. Same
idea as preview_home.py, scoped to the icons. Not a substitute for
on-hardware eyeballing, but catches shape/size problems for free.

Renders each condition icon at two sizes -- big (to inspect the shape) and
footer-size (how it will actually appear next to the temperature).
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import display  # noqa: E402
from preview_home import ImgFB  # noqa: E402

CONDS = ["clear", "partly", "cloudy", "fog", "drizzle", "rain", "rain_heavy",
         "snow", "thunder"]


def _to_logical(fb):
    """Undo the mount transform -> the portrait picture-frame view."""
    logical = Image.new("L", (display.LW, display.LH), 255)
    lp = logical.load()
    src = fb.px
    for ly in range(display.LH):
        for lx in range(display.LW):
            lp[lx, ly] = src[display.PHYS_W - 1 - ly, lx]
    return logical


def main():
    fb = ImgFB(display.PHYS_W, display.PHYS_H)
    row_f = display._fonts()["row"]

    # Portrait logical canvas is 480 wide (lx) x 800 tall (ly): lay the
    # icons out in a 2-column grid down the tall axis. Each cell shows the
    # glyph big (to judge shape) and footer-size (how it really appears).
    big = 86
    foot = 40
    cols = 2
    cell_w = display.LW // cols
    cell_h = 152
    margin = 16

    items = CONDS + ["drop"]
    for i, cond in enumerate(items):
        cx0 = (i % cols) * cell_w + margin
        cy0 = margin + (i // cols) * cell_h
        if cond == "drop":
            display._draw_drop(fb, cx0 + 24, cy0, big // 2)
            display._draw_drop(fb, cx0 + 24 + big // 2 + 20, cy0 + big // 4, foot // 2)
        else:
            display.draw_weather_glyph(fb, cond, cx0, cy0, big)
            display.draw_weather_glyph(fb, cond, cx0 + big + 20, cy0 + (big - foot) // 2, foot)
        display._text(fb, row_f, cond, cx0, cy0 + big + 8)

    logical = _to_logical(fb)
    out = os.path.join(os.path.dirname(__file__), "_preview_weather.png")
    logical.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
