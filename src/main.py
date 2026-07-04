"""Entry point: framebuffer alloc, boot flow, asyncio loop.

Boot flow (see CLAUDE.md "Architecture"): allocate framebuffer -> load
/config.json -> try Wi-Fi STA with stored creds -> on success, NTP sync +
draw the device IP once + start tasks; on failure, start the AP-mode
setup portal instead. The departure-fetch/redraw task isn't implemented
yet (sl.py/departures.py/display.py don't exist) -- today "start tasks"
means only the admin server.
"""

# Allocate the framebuffer FIRST, before any import, so the 48 KB block
# lands contiguously before other imports fragment the heap (see
# CLAUDE.md "Hardware" / RAM budget).
_FB_WIDTH = 800
_FB_HEIGHT = 480
fb_buf = bytearray(_FB_WIDTH * _FB_HEIGHT // 8)

import framebuf
import network
import asyncio
import ntptime
import time
import sys

import config
import wifi
import server
from epd7in5v2 import EPD7in5V2

fb = framebuf.FrameBuffer(fb_buf, _FB_WIDTH, _FB_HEIGHT, framebuf.MONO_HLSB)


def draw_boot_screen(ip):
    fb.fill(0)
    fb.text("Bus Display booted", 20, 20, 1)
    fb.text("Admin panel:", 20, 60, 1)
    fb.text("http://" + ip, 20, 80, 1)
    print("display: drew boot screen -- admin panel at http://%s" % ip)

    epd = EPD7in5V2()
    epd.init()
    epd.display(fb_buf)
    epd.sleep()


async def main():
    cfg = config.load()
    wifi_cfg = cfg.get("wifi")

    connected = False
    if wifi_cfg and wifi_cfg.get("ssid"):
        connected = wifi.connect_sta(wifi_cfg["ssid"], wifi_cfg.get("password", ""))

    if connected:
        try:
            ntptime.settime()
            print("main: NTP sync ok")
        except Exception as e:
            print("main: NTP sync failed:", e)

        ip = network.WLAN(network.STA_IF).ifconfig()[0]
        draw_boot_screen(ip)
    else:
        wifi.start_ap()
        print("main: no/failed Wi-Fi config -- setup form at http://192.168.4.1")

    print("main: starting admin server on port 80")
    await server.app.start_server(port=80)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    raise
except Exception as e:
    print("main: fatal error, idling for recovery (Ctrl-C for REPL):")
    sys.print_exception(e)
    while True:
        time.sleep(1)
