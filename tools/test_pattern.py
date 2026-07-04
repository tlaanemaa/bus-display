"""One-off hardware bring-up test for the 7.5" e-Paper V2 panel.

Confirms wiring, BUSY handling, and black/white polarity before any app
code exists. Requires epd7in5v2.py already deployed to the device:

    mpremote connect COMx fs cp src/epd7in5v2.py :epd7in5v2.py
    mpremote connect COMx run tools/test_pattern.py
"""
import framebuf
from epd7in5v2 import EPD7in5V2

WIDTH = 800
HEIGHT = 480

buf = bytearray(WIDTH * HEIGHT // 8)
fb = framebuf.FrameBuffer(buf, WIDTH, HEIGHT, framebuf.MONO_HLSB)

fb.fill(0)                                   # white background (bit 0 = white)
fb.rect(0, 0, WIDTH, HEIGHT, 1)               # 1px black border around the full screen
fb.fill_rect(0, 0, 100, 100, 1)               # solid black square, top-left corner ONLY
fb.text("BUS DISPLAY TEST OK", 120, 40, 1)    # black text, readable if polarity is correct

print("test pattern: white background, black border, solid black square "
      "in the top-left 100x100 corner only, text 'BUS DISPLAY TEST OK' near it")

epd = EPD7in5V2()
print("epd.init()")
epd.init()
print("epd.clear()")
epd.clear()
print("epd.display(buf)")
epd.display(buf)
print("epd.sleep()")
epd.sleep()

print("done -- on the panel you should see a black square ONLY in the "
      "top-left corner, a thin border around the whole screen, and "
      "readable black text. If colors are inverted (mostly black screen, "
      "missing square/border), see CLAUDE.md 'Pixel polarity' note. If "
      "nothing changes at all, check wiring and the BUSY pin first.")
