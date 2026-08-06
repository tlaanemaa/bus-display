# bus-display

A DIY bus departure display: a Waveshare 7.5" e-paper panel driven by an ESP32 (Waveshare's "Universal e-Paper Driver Board"), running MicroPython, showing real-time bus departures from Stockholm's SL Transport API.

## Status

- ✅ E-paper driver ported and confirmed working on hardware
- ✅ Minute-aligned ESP32 deep sleep with retained differential-refresh state
- ✅ Connects to USB-configured Wi-Fi and shows an explicit offline status if unavailable
- ✅ Fetches and displays real-time departures for a configurable, ordered list of stops — each with its next departure shown big and centered, the following two smaller
- ⬜ Polished onboarding — deliberately deferred; configuration currently uses local JSON over USB

## Hardware

- [Waveshare 7.5" e-Paper V2](https://www.waveshare.com/7.5inch-e-paper.htm) (800×480, black/white)
- [Waveshare Universal e-Paper Driver Board](https://www.waveshare.com/e-paper-esp32-driver-board.htm) (ESP32-WROOM-32, 4MB flash)

## Setup

Install host tools:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Flash MicroPython (download `ESP32_GENERIC-<version>.bin` from [micropython.org/download/ESP32_GENERIC](https://micropython.org/download/ESP32_GENERIC/)):

```
esptool --port COM3 erase-flash
esptool --port COM3 --baud 460800 write-flash 0x1000 ESP32_GENERIC-<version>.bin
```

Configure your stops and Wi-Fi (both local files are gitignored so private details cannot enter a public commit):

```
cp src/settings.example.json src/settings.json
# edit src/settings.json: your stop name(s) + SL site id(s) (find a site id via
# `curl https://transport.integration.sl.se/v1/sites` on your host, not the device)

# create src/config.json:
# {"wifi": {"ssid": "your-network", "password": "your-password"}}
```

Deploy:

```
deploy.bat COM3
```

The deploy builds the full runtime from `src/app.py` into `app.mpy`, copies all
bytecode/support files, then activates the release by copying the tiny source
`main.py` shim last. Keeping the 41.9 KB runtime out of on-device source
compilation is required for Wi-Fi and the 48 KB framebuffer to coexist on the
PSRAM-less ESP32.

Run the host checks before deploying:

```
.venv\Scripts\python -m mypy
.venv\Scripts\python -m pytest
```

There is no setup access point. If Wi-Fi credentials are missing or the network is unavailable, the panel says so and retries on the next minute-aligned wake.

## Reliability and required hardware acceptance

Settings and Wi-Fi configuration are validated at their JSON boundaries. In deep-sleep mode the precompiled `app.mpy` runtime constructs its watchdog, initializes Wi-Fi so the driver can reserve RX buffers, then allocates one framebuffer before RTC/NTP/API/render/panel work. It uses strict versioned retained state and commits a changed frame only after `encode → strict candidate decode → RTC invalidate → panel refresh/sleep → RTC commit`; unchanged frames do not wake the panel. Wi-Fi is reset per wake, network responses require 2xx status plus the expected JSON envelope and are always closed, and unexpected deep-sleep errors clear retained state before a bounded 60-second retry.

The reliability changes are host-verified; COM3 acceptance remains required before merge. Run the following without printing private settings or credentials:

```
.venv\Scripts\python -m mpremote connect list
.venv\Scripts\python -m mpremote connect COM3 fs ls
deploy.bat COM3
.venv\Scripts\python -m mpremote connect COM3 repl
```

Observe at least five minute-boundary wakes in serial and have the owner confirm the visible updates, full/partial refresh behavior, and panel sleep. Then verify an RTC-clear full-refresh recovery and an owner-approved Wi-Fi outage/recovery before merging.

## Repo layout

```
src/      device filesystem root (`main.py` shim + `app.py` runtime + support)
tests/    pytest, runs on host CPython
tools/    host-side scripts (hardware bring-up, one-off experiments)
```

See [AGENTS.md](AGENTS.md) for hardware details, verified gotchas, and full architecture notes.
