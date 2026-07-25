# bus-display

A DIY Stockholm bus-departure display: a Waveshare 7.5-inch V2 e-paper panel
on an ESP32 Universal e-Paper Driver Board, running MicroPython.

The firmware has one synchronous deep-sleep wake cycle. It validates local
configuration, reserves its one framebuffer before any radio or RTC work,
restores compatible semantic screen state, connects, waits for exactly `:00`,
fetches each source once, updates the panel only when the rendered result
changes, safely commits retained state, and sleeps until the next minute.

The panel shows clear Wi-Fi status when it cannot connect; the next wake tries
again. Configuration is USB-only—there is no network provisioning mode.

## Hardware

- [Waveshare 7.5-inch e-Paper V2](https://www.waveshare.com/7.5inch-e-paper.htm)
  (800x480, black/white)
- [Waveshare Universal e-Paper Driver Board](https://www.waveshare.com/e-paper-esp32-driver-board.htm)
  (ESP32-WROOM-32, 4 MB flash)

## Setup

Install the host tools:

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Flash MicroPython (download `ESP32_GENERIC-<version>.bin` from
[micropython.org/download/ESP32_GENERIC](https://micropython.org/download/ESP32_GENERIC/)):

```text
esptool --port COM3 erase-flash
esptool --port COM3 --baud 460800 write-flash 0x1000 ESP32_GENERIC-<version>.bin
```

Create the two private, gitignored configuration files:

```text
Copy-Item src/settings.example.json src/settings.json
# Edit src/settings.json with the stop name(s) and SL site id(s).
# Find a site id on the host, never on the device:
# curl http://transport.integration.sl.se/v1/sites

# Create src/config.json:
# {"wifi": {"ssid": "your-network", "password": "your-password"}}
```

Run the checks and deterministic USB deploy:

```text
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
deploy.bat COM3
```

`deploy.bat` precompiles the explicit firmware module set, removes retired
device artifacts, and uploads `main.py`, bytecode, settings, Wi-Fi config,
and streamed font files before resetting the board. Close any serial console
first; only one process can hold the USB port.

## Display and refresh behavior

The large, left-aligned Bitter countdown is the primary information for each
configured stop. Smaller line/destination and following-departure rows give
context. A stop with a failed request is marked `STALE` without obscuring the
other stop. The footer puts weather on the left and local date/time on the
right. A full refresh is used for the first compatible-state failure and at
the configured ghost-clearing interval; normal changed minute updates are
true differential partial refreshes using one semantic retained frame.

See [AGENTS.md](AGENTS.md) for the verified hardware contract, retained-state
compatibility rules, screen geometry, and maintenance guidance.
