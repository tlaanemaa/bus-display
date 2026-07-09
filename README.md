# bus-display

A DIY bus departure display: a Waveshare 7.5" e-paper panel driven by an ESP32 (Waveshare's "Universal e-Paper Driver Board"), running MicroPython, showing real-time bus departures from Stockholm's SL Transport API.

## Status

- ✅ E-paper driver ported and confirmed working on hardware
- ✅ Wi-Fi provisioning: connects to a saved network, or serves a setup form over its own AP if it can't
- ✅ Fetches and displays real-time departures for a configurable, ordered list of stops — each with its next departure shown big and centered, the following two smaller
- ⬜ Admin panel beyond Wi-Fi setup — not built yet; stop/refresh config is a local JSON file instead (see Setup)

## Hardware

- [Waveshare 7.5" e-Paper V2](https://www.waveshare.com/7.5inch-e-paper.htm) (800×480, black/white)
- [Waveshare Universal e-Paper Driver Board](https://www.waveshare.com/e-paper-esp32-driver-board.htm) (ESP32-WROOM-32, 4MB flash)

## Setup

Install host tools:

```
pip install esptool mpremote pytest mpy-cross
```

Flash MicroPython (download `ESP32_GENERIC-<version>.bin` from [micropython.org/download/ESP32_GENERIC](https://micropython.org/download/ESP32_GENERIC/)):

```
esptool --port COM3 erase-flash
esptool --port COM3 --baud 460800 write-flash 0x1000 ESP32_GENERIC-<version>.bin
```

Configure your stops (this file is gitignored — it's not in the repo on purpose, so your stop doesn't end up in a public commit):

```
cp src/settings.example.json src/settings.json
# edit src/settings.json: your stop name(s) + SL site id(s) (find a site id via
# `curl https://transport.integration.sl.se/v1/sites` on your host, not the device)
```

Deploy:

```
cd src && mpremote connect COM3 fs cp -r . :
mpremote connect COM3 reset
```

On first boot (no saved Wi-Fi), the device starts an open access point, **BusDisplay-Setup**. Connect to it and go to `http://192.168.4.1` to enter your Wi-Fi credentials; the device saves them and reboots onto your network.

## Repo layout

```
src/      device filesystem root (MicroPython code + vendored lib/)
tests/    pytest, runs on host CPython
tools/    host-side scripts (hardware bring-up, one-off experiments)
```

See [CLAUDE.md](CLAUDE.md) for hardware details, verified gotchas, and full architecture notes.
