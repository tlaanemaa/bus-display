"""Render the whole home screen on the host, exactly as the owner sees
it, so layout/legibility can be judged before a slow deploy->reset->
eyeball cycle. Drives the real display.draw_home() through a fake 800x480
physical framebuffer, then maps physical->logical (undoing the 90deg
mount transform) to a 480x800 portrait PNG -- the picture-frame view.

Not a substitute for on-hardware eyeballing (e-paper contrast/ghosting
differ), but catches layout/truncation/overflow for free."""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import display  # noqa: E402


class ImgFB:
    """Minimal fb over an 800x480 buffer: fill(0)=white bg, color 1=black
    (the panel's convention, CLAUDE.md pixel polarity)."""

    def __init__(self, w, h):
        self.width, self.height = w, h
        self.img = Image.new("L", (w, h), 255)
        self.px = self.img.load()

    def fill(self, color):
        v = 0 if color else 255
        for y in range(self.height):
            for x in range(self.width):
                self.px[x, y] = v

    def fill_rect(self, x, y, w, h, color):
        v = 0 if color else 255
        for yy in range(y, y + h):
            if 0 <= yy < self.height:
                for xx in range(x, x + w):
                    if 0 <= xx < self.width:
                        self.px[xx, yy] = v

    def pixel(self, x, y, color=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return 0
        if color is None:
            return 1 if self.px[x, y] == 0 else 0
        self.px[x, y] = 0 if color else 255


def _deps(*rows):
    import departures
    raw = {"departures": [
        {"line": {"designation": ln}, "destination": d, "display": disp,
         "expected": "2026-07-11T%02d:00:00" % i}
        for i, (ln, d, disp) in enumerate(rows)]}
    return departures.parse_departures(raw)


def main():
    sections = [
        display.stop_section("Molnvik", _deps(
            ("474", "Slussen", "7 min"), ("440", "Slussen", "14 min"), ("425", "Nacka forum", "21 min"))),
        display.stop_section("Grisslinge", _deps(
            ("471", "Gustavsberg centrum", "Nu"), ("474", "Slussen", "16 min"), ("469", "Alstaket", "24 min"))),
    ]
    footer = display.footer_lines("Fri 11 Jul", "23:41")
    weather = {"condition": "rain", "tmin": 6, "tmax": 12, "precip": 60}

    fb = ImgFB(display.PHYS_W, display.PHYS_H)
    content_bottom, footer_top = display.draw_home(fb, sections, footer, weather)
    print("content_bottom=%d footer_top=%d (fits=%s)" % (
        content_bottom, footer_top, content_bottom <= footer_top))

    # physical(px,py) came from logical(lx,ly) via px=799-ly, py=lx.
    # Invert to show the portrait picture-frame view: logical(lx,ly).
    logical = Image.new("L", (display.LW, display.LH), 255)
    lp = logical.load()
    src = fb.px
    for ly in range(display.LH):
        for lx in range(display.LW):
            lp[lx, ly] = src[display.PHYS_W - 1 - ly, lx]
    out = os.path.join(os.path.dirname(__file__), "_preview_home.png")
    logical.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
