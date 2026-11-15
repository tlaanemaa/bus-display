# Frozen-module manifest for the bus display firmware.
#
# Everything ESP32_GENERIC freezes (asyncio, requests, ...) plus the app's own
# modules from src/. Frozen bytecode runs from flash instead of being loaded
# into RAM, which hands that RAM to the ESP-IDF pool the Wi-Fi stack needs
# (CLAUDE.md "RAM budget"). main.py and boot.py are NOT frozen: main.py stays a
# file on the board (MicroPython runs it by name) and boot.py is the stock one.
#
# Files on the board shadow frozen modules (sys.path is ['', '.frozen', ...]),
# so a .mpy copied onto the board overrides the frozen copy -- handy for
# trying a change without reflashing, but it costs that module's RAM again.
include("$(PORT_DIR)/boards/manifest.py")

SRC = "$(BOARD_DIR)/../../../src"

freeze(SRC, (
    "app.py",
    "bitfont.py",
    "config.py",
    "departures.py",
    "display.py",
    "epd7in5v2.py",
    "fieldlog.py",
    "localtime.py",
    "logship.py",
    "openmeteo.py",
    "server.py",
    "settings.py",
    "sl.py",
    "weather.py",
    "wifi.py",
))
freeze(SRC + "/lib", "microdot.py")
