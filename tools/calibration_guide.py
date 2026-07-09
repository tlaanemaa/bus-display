"""Calibration guide, v2 -- zeroing in on the picture frame's visible-area
cutoff. NOT part of the app. The panel is mounted rotated for portrait
viewing (same 90-degree transform as src/display.py -- see CLAUDE.md
"Physical mounting & drawable area" for the derivation).

v1 (tick ruler on all 4 edges) gave rough readings: left barely visible
(near-zero crop), top numbers right at the edge (~30-40px crop), right
correct (near-zero crop), bottom visible only up to ~700-760 out of 799
(~40-100px crop). v2's rect (L15 T45 R12 B55) was confirmed fully within
the drawable area. This version shifts that rect ~0.5mm right and expands
it ~2mm on every side (converted at the panel's published ~0.2040 mm/px
dot pitch, so 1cm is ~49px -- approximate, trust a physical ruler over
this number). A thin reference line still marks the true buffer edge, so
you can eyeball whether the bigger rectangle still clears the frame on
all 4 sides.

    mpremote connect COMx run tools/calibration_guide.py

Report back per side: "good" / "still clipped, push in more" / "lots of
spare white, can push out" -- and roughly how much if it needs to move.
"""
import framebuf
from epd7in5v2 import EPD7in5V2

WIDTH = 800    # physical buffer, landscape, fixed by the panel
HEIGHT = 480

# Logical portrait canvas as the viewer sees it upright: 480 wide x 800 tall.
# Same 90-degree transform as src/display.py -- physical (px, py) =
# (WIDTH - 1 - ly, lx) for a logical point (lx, ly).
LW = HEIGHT   # 480
LH = WIDTH    # 800

# Candidate safe margins (logical px). Previous rect (L7 T35 R0 B45) was the
# last confirmed step; this grows the height by ~1mm, split evenly top and
# bottom, at ~4.9 px/mm (0.204mm/px dot pitch, approximate -- trust a
# physical ruler over this number).
_SHIFT_RIGHT = 2   # ~0.5mm
_EXPAND = 10       # ~2mm
_EXPAND_HEIGHT_EACH = 2  # ~0.5mm, split from ~1mm total height growth
MARGIN_LEFT = 15 + _SHIFT_RIGHT - _EXPAND
MARGIN_TOP = 45 - _EXPAND - _EXPAND_HEIGHT_EACH
MARGIN_RIGHT = 12 - _SHIFT_RIGHT - _EXPAND
MARGIN_BOTTOM = 55 - _EXPAND - _EXPAND_HEIGHT_EACH

buf = bytearray(WIDTH * HEIGHT // 8)
physical = framebuf.FrameBuffer(buf, WIDTH, HEIGHT, framebuf.MONO_HLSB)
physical.fill(0)


def lpixel(lx, ly, c=1):
    physical.pixel(WIDTH - 1 - ly, lx, c)


def lrect(lx0, ly0, lw, lh, c=1):
    """Outline of a logical rect with top-left (lx0, ly0), size lw x lh."""
    px0 = WIDTH - ly0 - lh
    py0 = lx0
    physical.rect(px0, py0, lh, lw, c)


def ltext(s, lx, ly, c=1):
    """Draw a short string with its logical top-left at (lx, ly)."""
    tw, th = 8 * len(s), 8
    tbuf = bytearray(tw * th // 8)
    tfb = framebuf.FrameBuffer(tbuf, tw, th, framebuf.MONO_HLSB)
    tfb.text(s, 0, 0, 1)
    for ty in range(th):
        for tx in range(tw):
            if tfb.pixel(tx, ty):
                lpixel(lx + tx, ly + ty, c)


# true buffer edge -- reference for "this is all the panel can draw, before
# any frame cropping"
physical.rect(0, 0, WIDTH, HEIGHT, 1)

# candidate safe content rectangle
safe_w = LW - MARGIN_LEFT - MARGIN_RIGHT
safe_h = LH - MARGIN_TOP - MARGIN_BOTTOM
lrect(MARGIN_LEFT, MARGIN_TOP, safe_w, safe_h, 1)

# label each side of the safe rect with the margin (px) used, just inside it
ltext("L%d" % MARGIN_LEFT, MARGIN_LEFT + 4, MARGIN_TOP + 4, 1)
ltext("T%d" % MARGIN_TOP, LW // 2 - 12, MARGIN_TOP + 4, 1)
ltext("R%d" % MARGIN_RIGHT, LW - MARGIN_RIGHT - 28, MARGIN_TOP + 4, 1)
ltext("B%d" % MARGIN_BOTTOM, LW // 2 - 12, LH - MARGIN_BOTTOM - 12, 1)

print("calibration guide v2: safe rect margins L=%d T=%d R=%d B=%d "
      "(logical px) inside a %dx%d canvas" %
      (MARGIN_LEFT, MARGIN_TOP, MARGIN_RIGHT, MARGIN_BOTTOM, LW, LH))

epd = EPD7in5V2()
print("epd.init()"); epd.init()
print("epd.clear()"); epd.clear()
print("epd.display(buf)"); epd.display(buf)
print("epd.sleep()"); epd.sleep()
print("done")
