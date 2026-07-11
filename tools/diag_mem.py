"""Diagnose the RAM-vs-HTTPS conflict with the streamed fonts: measure
free heap AND largest contiguous block (what mbedtls's RSA handshake
actually needs) before the first fetch, after the first draw, and before
a second fetch -- to see exactly what the draw leaves behind that starves
the next TLS handshake.

    mpremote connect COM3 run tools/diag_mem.py
"""
import gc
import time
import framebuf
import config
import settings
import wifi
import sl
import display
import departures
from epd7in5v2 import EPD7in5V2


def maxblock():
    gc.collect()
    lo, hi, best = 0, 180000, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        try:
            b = bytearray(mid)
            del b
            best = mid
            lo = mid + 1
        except MemoryError:
            hi = mid - 1
    gc.collect()
    return best


def report(tag):
    gc.collect()
    print("[%s] free=%d  maxblock=%d" % (tag, gc.mem_free(), maxblock()))


cfg = settings.load()
wcfg = config.load().get("wifi")
print("connecting wifi...")
wifi.connect_sta(wcfg["ssid"], wcfg.get("password", ""))
time.sleep(2)
stop = cfg["stops"][0]

report("boot, before any fetch")


def fetch_all():
    out = []
    for s in cfg["stops"]:
        raw = sl.fetch_departures(s["site_id"], forecast=cfg["forecast_min"], direction=cfg["direction_code"])
        out.append(departures.parse_departures(raw)[:cfg["departures_per_stop"]])
    return out


try:
    all_deps = fetch_all()
    print("fetch #1 OK:", [len(d) for d in all_deps])
except Exception as e:
    print("fetch #1 FAILED:", e)
    all_deps = [[] for _ in cfg["stops"]]

report("after fetch #1, before draw")

# Faithful draw+refresh: real content for every stop + a real full EPD
# refresh over SPI (mirrors _draw_and_refresh's full path), fb freed after.
sections = [display.stop_section(s["name"], d) for s, d in zip(cfg["stops"], all_deps)]
footer = display.footer_lines("Sat 11 Jul", "23:45")
epd = EPD7in5V2()
buf = bytearray(800 * 480 // 8)
fb = framebuf.FrameBuffer(buf, 800, 480, framebuf.MONO_HLSB)
display.draw_home(fb, sections, footer)
epd.init()
epd.display(buf)
epd.sleep()
del fb, buf
gc.collect()

report("after 1 full refresh (fb freed)")

try:
    fetch_all()
    print("fetch #2 OK")
except Exception as e:
    print("fetch #2 FAILED:", e)

report("after fetch #2 attempt")
print("DIAG DONE")
