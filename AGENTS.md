# AGENTS.md

## Project

DIY Stockholm bus-departure display: a Waveshare 7.5-inch V2 black/white
e-paper panel on the Waveshare Universal e-Paper Driver Board (ESP32-WROOM-32,
4 MB flash, no PSRAM), running MicroPython. The physical panel is mounted
portrait in a cropped picture frame.

The verification loop is deploy -> reset -> watch serial -> ask the owner to
inspect the panel. Each successful redraw prints the same semantic content to
serial, so logs corroborate what is visible.

## Hardware contract

- Panel: 800x480 Waveshare 7.5-inch V2. A full refresh takes roughly 4–5 s
  and flashes black/white normally.
- SPI pins are fixed by this driver board: BUSY=25, RST=26, DC=27, CS=15,
  SCK=13, MOSI=14. Use `SPI(2, baudrate=4_000_000, sck=Pin(13),
  mosi=Pin(14))`; there is no MISO.
- BUSY is active-low. The driver must send `0x71` before every BUSY pin read;
  polling the pin alone can hang.
- Wire polarity is hardware-confirmed: 0 is white and 1 is black. With
  `framebuf.MONO_HLSB`, use `fill(0)` for white and draw colour 1 for black.
- A full refresh sends both planes: `0x10` receives the bitwise-not image,
  `0x13` the image, then `0x12` triggers the panel. Do not alter the verified
  command sequence in `src/epd7in5v2.py`.
- Always put the panel to sleep after a refresh. `_draw_and_refresh` protects
  this with `finally`; retain that transaction boundary.

The panel's logical canvas is 480x800. Logical `(lx, ly)` maps to physical
`(799 - ly, lx)`. The safe logical margins are left=7, top=33, right=0, and
bottom=43. Never render a visible border or crop outline.

## Product operation

There is one synchronous, deep-sleep-only wake path:

```text
boot -> validate settings/config -> reserve framebuffer -> restore compatible
semantic state -> connect -> wait until :00 -> fetch once -> decide refresh
-> invalidate/refresh/commit when changed -> deep sleep
```

`main.py` reserves the only 48 KB framebuffer immediately after configuration
validation and before RTC decode, radio work, NTP, or requests. Heap
fragmentation means this reservation must happen on the clean boot heap. Never
hold a second full-size buffer. The retained data is compact semantic screen
content, never pixels; after wake the prior semantic frame is rendered into
the same buffer for a differential old plane, then the new frame reuses it.

Wakes occur every minute. `power.wake_advance_s` defaults to 3, waking early
enough to prepare Wi-Fi, but requests are held until exactly `HH:MM:00`.
`wake_advance_s: 0` retains a boundary wake. Each adapter gets one bounded
request per wake; the next wake is the retry. NTP is synchronized on a
connected cold boot and then at most daily after the request boundary. Its
failure does not advance the last-success marker. SL's server-provided
departure strings remain authoritative even if the local footer clock drifts.

Missing credentials or unavailable Wi-Fi produces a visible `Wi-Fi
unavailable` footer state and the next minute's wake tries again. Configuration
is USB-only: `/settings.json` controls stops/cadence and `/config.json` holds
Wi-Fi credentials. Both are local, gitignored files; start from
`src/settings.example.json` for settings. There is no network provisioning
mode.

## Retained state and panel transaction

RTC state uses retained format v2 and is accepted only when its format version,
renderer revision, settings fingerprint, checksum, and ordered stop identities
all match. `retained.RENDER_REVISION` must change whenever semantic rendering
meaning changes. The settings fingerprint covers every setting that changes a
rendered frame; an incompatibility deliberately forces exactly one full
refresh, and the following changed wake can use differential refresh again.

For a changed frame, `refresh_txn.apply` encodes the new semantic state,
clears RTC state, refreshes the panel, then writes and readback-validates the
new state. A failed refresh therefore cannot leave a false old/new pairing for
the next partial update. Unchanged content skips the panel write but still
commits validated state. Partial refreshes are normal per-minute updates;
`full_refresh_interval_min` (default 60) periodically clears ghosting.

## Source layout and implementation rules

```text
src/
  main.py          synchronous boot/wake flow and framebuffer lifetime
  cycle.py         pure per-wake display and refresh policy
  refresh_txn.py   RTC invalidate -> panel refresh -> verified commit ordering
  retained.py      validated semantic RTC state and compatibility checks
  config.py        /config.json Wi-Fi validation
  settings.py      /settings.json validation
  wifi.py          bounded station connection
  display.py       portrait layout and serial frame summaries
  bitfont.py       streamed 1-bit font reader
  fonts/           generated Bitter .fnt files, read from flash
  epd7in5v2.py     verified Waveshare V2 driver port
  sl.py            SL departures HTTP adapter
  openmeteo.py     Open-Meteo HTTP adapter
  departures.py, weather.py, localtime.py, models.py
                   pure parsing, policy, time, and type data
tests/             host pytest suite for pure logic
tools/             host bring-up, calibration, previews, and font generation
```

Keep pure parsing, formatting, layout, state, and time math free of hardware
or network imports so it remains host-testable. Every first-party `src/`
module is checked by mypy. Runtime annotations use built-ins and quoted
expressions; typing imports stay under `if False:` for MicroPython and code
must also compile with `mpy-cross`.

SL and Open-Meteo use plain HTTP because they provide public, keyless data and
the devices do not need a credential-bearing transport for these requests.
Precompile every module except `main.py` on the host because on-device source
compilation fragments the small heap and jeopardizes the early framebuffer
reservation. This is a durable deployment and memory rule, not retry policy.

## Display design

The display is a glanceable kitchen-counter screen. Each configured stop has a
label and rule, then a large left-aligned Bitter hero countdown with its unit
baseline-aligned beside it, followed by a line badge/destination and compact
remaining rows. A per-stop inverted `STALE` badge marks only the stop whose
request failed. Sections are separated by whitespace (`GROUP_GAP=48`), not a
divider.

The streamed Bitter font roles are fixed: hero (~87 px, weight 800), heading
(~35 px, weight 700), and row/footer (~27 px, weight 500). Glyphs are read
from flash one at a time; do not replace them with resident font modules.
`CONTENT_MARGIN = 14` inside the crop-safe region. The footer is one compact
status line: weather cluster left and date/time right. It shows explicit
Wi-Fi or weather errors only when no usable state can be shown.

## Data rules

Settings contain an ordered list of one or two stops. Each stop is fetched
independently, server-filtered by direction, parsed, and sorted by `expected`.
An individual failure keeps its retained departures and marks that stop stale;
an empty successful response renders `No departures`. SL's `display` string
is shown as supplied. Default `forecast_min` is 180 and the server maximum is
1200. Poll around once per minute; site lookup is host-only because its
response is too large for the device.

Weather is optional in settings and is fetched at its configured slow interval
(default 30 minutes). Open-Meteo requests only today's small daily/hourly
fields. The footer uses daytime (07:00–23:00 local) weather-code mode and
maximum precipitation probability; daily high/low remain daily values. A
same-day, fresh last-good reading may survive a failed fetch for at most
`max_age_min` (default 180); otherwise the explicit weather error is shown.

## Toolchain and deployment

Set up the host environment with:

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Use the virtual environment for checks:

```text
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
```

`deploy.bat [COM_PORT]` is the deterministic deploy path. It precompiles its
explicit module list, verifies the USB connection, removes retired device
artifacts, uploads `main.py`, bytecode, `/settings.json` when present,
`/config.json` when present, and font files, then resets the board. Close any
serial console first. `main.py` is intentionally source because MicroPython
boots that filename; the other modules ship as `.mpy`.

Do not access the panel during host-only work. Hardware acceptance requires
the owner to inspect the display after deployment, including serial/panel
agreement, panel sleep after refresh, two retained differential updates, a
single incompatibility-triggered full update followed by a partial one, and
visible automatic recovery from unavailable Wi-Fi.
