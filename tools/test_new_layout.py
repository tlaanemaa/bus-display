"""On-device smoke test for the departures screen: draws one static frame
from sample data using the REAL display.py/departures.py (no duplicate
layout logic here -- see CLAUDE.md "Testability rule" and the
host-side tests/test_display.py) so this exercises exactly the code path
main.py uses, minus the network fetch. Useful to eyeball a layout change
on the real panel before wiring a change into the live loop.

Run WITHOUT copying:  python -m mpremote connect COM3 run tools/test_new_layout.py
Requires on the device already: display.py, departures.py, epd7in5v2.py.
"""
import gc
import framebuf

import display
import departures
from epd7in5v2 import EPD7in5V2

RAW_1 = {"departures": [
    {"line": {"designation": "474"}, "destination": "Slussen", "display": "4 min", "expected": "2026-07-10T14:36:00"},
    {"line": {"designation": "440"}, "destination": "Slussen", "display": "12 min", "expected": "2026-07-10T14:44:00"},
    {"line": {"designation": "425"}, "destination": "Nacka", "display": "19 min", "expected": "2026-07-10T14:51:00"},
]}
RAW_2 = {"departures": [
    {"line": {"designation": "471"}, "destination": "Slussen", "display": "7 min", "expected": "2026-07-10T14:39:00"},
    {"line": {"designation": "474"}, "destination": "Slussen", "display": "16 min", "expected": "2026-07-10T14:48:00"},
    {"line": {"designation": "469"}, "destination": "Ålstäket", "display": "24 min", "expected": "2026-07-10T14:56:00"},
]}


def main():
    gc.collect()
    print("free before fb:", gc.mem_free())

    sections = [
        display.stop_section("Mölnvik", departures.parse_departures(RAW_1)),
        display.stop_section("Grisslinge", departures.parse_departures(RAW_2)),
    ]
    footer = display.footer_lines("Fre 10 jul", "14:32")

    buf = bytearray(display.PHYS_W * display.PHYS_H // 8)
    fb = framebuf.FrameBuffer(buf, display.PHYS_W, display.PHYS_H, framebuf.MONO_HLSB)
    display.draw_home(fb, sections, footer)

    epd = EPD7in5V2()
    epd.init()
    epd.display(buf)
    epd.sleep()
    print("test_new_layout: refreshed + panel asleep")


main()
