"""Boot stub. Its whole job is ORDER, because this PSRAM-less board's RAM only
just fits (all measured on hardware 2026-11-08):

1. Allocate the 48 KB framebuffer FIRST. MicroPython's heap grows in chunks
   and never compacts; once the app is loaded no 48 KB contiguous block is
   left (boot-time MemoryError, which froze the display).
2. Connect Wi-Fi SECOND, before loading the app. The Wi-Fi/lwIP stack and the
   growing MicroPython heap share one ESP-IDF memory pool; if the app's ~48 KB
   of modules are loaded first, the Wi-Fi driver is left too little and every
   DNS lookup fails (getaddrinfo -202).
3. Collect garbage EARLY (gc.threshold): left alone, MicroPython grows its
   heap into that same shared pool rather than collecting first, and growth is
   never given back. One SL fetch grew it ~25 KB and starved the Wi-Fi stack;
   with a 4 KB threshold the heap stays put (measured).
4. Load the app LAST. It lives in app.py (precompiled to app.mpy by
   deploy.bat), because main.py is the one file the board compiles from source
   and compiling fragments the heap -- keep this file tiny.
"""
import gc
gc.collect()
gc.threshold(4096)
fb_buf = bytearray(800 * 480 // 8)

import config
import wifi
boot_wifi = wifi.boot_connect(config.load().get("wifi"))

import app
app.start(fb_buf, boot_wifi)
