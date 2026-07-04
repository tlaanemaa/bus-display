# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A DIY bus departure display: a Waveshare 7.5" e-paper panel driven by an ESP32, running MicroPython, showing real-time departures from Stockholm's SL Transport API. The spec is discovered incrementally with the owner — this file is the source of truth for decisions already made. **When a design decision is settled in conversation, record it here** (update "Decisions" / remove it from "Open questions").

You cannot see the physical screen. Verification loop: deploy → reset → watch serial output → ask the owner to look at the panel. Make the code corroborate the screen: whenever the display is redrawn, print the same content as text to serial so logs alone can confirm most behavior.

## Hardware (fixed facts — do not rediscover these)

- **Board**: Waveshare "Universal e-Paper Driver Board" — ESP32-WROOM-32, 4MB flash, no PSRAM. Appears as a COM port over USB.
- **Panel**: Waveshare 7.5" e-Paper **V2**, 800×480, black/white only, SPI, controller embedded on panel. Full refresh takes ~4–5 s and flashes black/white; that is normal.
- **Panel-to-ESP32 wiring is fixed by the board** (non-standard SPI pins — must be set explicitly, defaults won't work):
  - BUSY=25, RST=26, DC=27, CS=15, SCK/CLK=13, MOSI/DIN=14 (no MISO — display is write-only)
  - `SPI(2, baudrate=4_000_000, sck=Pin(13), mosi=Pin(14))` works; ESP32 GPIO matrix allows any pins.
- **7.5" V2 BUSY is active-low, and polling the pin alone is not enough**: the panel is busy while BUSY reads 0, and the reference driver sends command `0x71` (status read) before *each* read of the pin inside the wait loop — without the 0x71 the flag doesn't update and the wait can hang forever.
- **Pixel polarity (7.5" V2, confirmed byte-for-byte against the reference source, not just its docstring)**: at the wire, bit **0 = white, 1 = black** — `Clear()` sends `0x00` bytes on the `0x13` plane to whiten the screen; the driver's `getbuffer()` XOR-inverts PIL data (where 1=white) before sending. So with `framebuf.FrameBuffer(buf, 800, 480, framebuf.MONO_HLSB)`: `fill(0)` = white background, draw with color 1 = black. Still confirm with a test pattern on first bring-up — see `tools/test_pattern.py`.
- **Every full refresh writes TWO data planes, not one**: command `0x10` gets the bitwise-NOT of the image (the "old"/previous-image plane the controller needs internally), then command `0x13` gets the real image, then `0x12` triggers the actual refresh. Sending only `0x13` is not sufficient — `Clear()` and `display()` in the reference driver both always write both planes before `0x12`.
- **Reference driver already ported** — see `src/epd7in5v2.py`. It's a line-for-line MicroPython translation of Waveshare's `epd7in5_V2.py` (fetched raw from https://github.com/waveshareteam/e-Paper, RaspberryPi_JetsonNano/python/lib/waveshare_epd/, 2026-07-04): same command bytes/order for `init()`, `clear()`, `display()`, `sleep()`, same two-plane write, same `0x71`-before-each-busy-read polling. **Do not "improve" or re-derive the init sequence** — if the screen misbehaves, the bug is almost certainly in wiring, framebuffer content, or call order, not in the command bytes. `init_fast`/`init_part`/4-gray/partial-refresh from the reference driver are intentionally not ported (see Open questions) — add them only when actually needed. **Already confirmed working on real hardware** (2026-07-04): flashed, wired, ran `tools/test_pattern.py` — owner confirmed sharp, correctly-positioned, correctly-polarized output.
- **No ready-made MicroPython driver exists for this panel+board combo — checked, don't re-search for one.** Waveshare's own repo (link above) only has Arduino/STM32/RaspberryPi_JetsonNano folders, no MicroPython. The most common community port, https://github.com/mcauser/micropython-waveshare-epaper, has `epaper7in5.py` but no V2 variant (predates this panel revision's different init sequence). The port in `src/epd7in5v2.py` — translating Waveshare's own RPi driver — **is** the "use Waveshare's solution" path for this hardware; it's not a workaround. The pin mapping came from the GxEPD2 Arduino reference for this exact board (also cited above), not guesswork, and is now hardware-confirmed too.
- RAM budget: **165,632 bytes free heap measured** right after boot (`gc.collect(); gc.mem_free()`) on this board with MicroPython v1.28.0 ESP32_GENERIC, before any app code runs. The 48 KB framebuffer must be allocated **once, first thing in main.py**, before Wi-Fi/imports fragment the heap.
- **Never hold two BUF_SIZE (48,000-byte) buffers alive at once — MicroPython's allocator doesn't defragment.** Hit this directly: `epd7in5v2.py`'s first `display()` implementation allocated a second 48 KB buffer (for the inverted "old" plane) while the framebuffer was still alive, and it threw `MemoryError` despite ~165 KB nominally free — a large contiguous block can fail to find room even when the sum of free memory looks fine, because freed/live allocations of varying sizes fragment the heap and nothing compacts it. Fix (already applied): stream large writes through a small reusable scratch buffer (`_write_bulk_inverted`/`_write_fill` in `epd7in5v2.py`, chunked at 512 bytes) instead of ever allocating a second full-size buffer. Apply the same pattern to any future code that transforms the framebuffer (e.g. partial-refresh diffing) — chunk it, don't duplicate it.

## E-paper rules (breaking these damages the panel or wastes hours)

1. Call the driver's `sleep()` after every refresh. Leaving the panel powered in an active state degrades it.
2. Full refresh only, and only when displayed content actually changed (compare rendered text, not raw API responses — `expected` timestamps jitter). Departure minutes change ~1×/min; that's the natural cadence. Waveshare conservatively recommends ≥180 s between refreshes — the owner tunes this trade-off, don't silently decide.
3. Init → write buffer → display/turn-on → sleep, every cycle. After `sleep()` the panel needs re-init before the next refresh.
4. The refresh is a blocking busy-wait (~5 s). Acceptable; don't try to make the SPI driver async.

## Toolchain & commands

Host tools (install once): `pip install esptool mpremote pytest`

```
mpremote connect list                          # find the COM port (call it COM3 below)
```

One-time firmware flash (download ESP32_GENERIC .bin from micropython.org/download/ESP32_GENERIC — plain 4MB variant, not SPIRAM/OTA):

```
esptool --port COM3 erase_flash
esptool --port COM3 --baud 460800 write_flash 0x1000 ESP32_GENERIC-<version>.bin
```

(esptool v5 prefers hyphenated `erase-flash`/`write-flash`; underscore aliases still work. If it can't connect, hold the board's BOOT button while it retries.)

Everyday loop:

```
cd src && mpremote connect COM3 fs cp -r . :   # deploy everything (bash; older mpremote may reject "cp -r ." — then copy files/dirs explicitly)
mpremote connect COM3 fs cp src/main.py :main.py   # deploy a single changed file (faster, prefer this)
mpremote connect COM3 reset                    # restart so new code runs (cp does NOT restart)
mpremote connect COM3 repl                     # serial console; Ctrl-] exits, Ctrl-D soft-reboots, Ctrl-C interrupts main.py
mpremote connect COM3 run tools/somescript.py  # run a host-side file on the device WITHOUT copying — ideal for hardware experiments
mpremote connect COM3 fs ls                    # verify what's on the device
pytest                                         # host-side tests for the pure-logic modules
```

Gotchas: only one process can hold the COM port — close any open REPL before deploying. `main.py` auto-runs on boot; structure it so Ctrl-C reaches the REPL (catch exceptions, print, idle — never crash into a tight reset loop, or the board becomes hard to reflash).

## Architecture

Single asyncio event loop (`asyncio` in MicroPython) running two long-lived tasks: the departure-fetch/redraw cycle and the admin web server. No threads.

Boot flow: allocate framebuffer → load `/config.json` → try Wi-Fi STA with stored creds (~15 s timeout) → on success: NTP sync, draw the device's IP on the panel once so the owner can find the admin panel, start tasks. On failure: start AP mode (SSID `BusDisplay-Setup`, portal at `http://192.168.4.1`) serving a form for SSID/password → save to `/config.json` → reboot. Wi-Fi creds therefore only ever exist on the device, never in the repo.

Intended layout (`src/` maps 1:1 to the device filesystem root; adjust as the project evolves):

```
src/
  main.py          # entry: framebuffer alloc, boot flow, asyncio loop
  config.py        # load/save /config.json on device
  wifi.py          # STA connect with timeout, AP fallback
  server.py        # admin panel + config API (Microdot)
  display.py       # layout/rendering onto the framebuf; also logs what it drew
                   #   (keep layout sectioned — departures are the first content source, not the only one; see Future direction)
  epd7in5v2.py     # low-level panel driver (port of Waveshare's, pins above)
  sl.py            # thin I/O wrapper: fetch departures JSON over HTTPS
  departures.py    # PURE logic: parse/filter/format SL JSON — no hardware imports
  lib/             # vendored third-party (microdot.py, writer.py, generated fonts) — don't hand-edit
tests/             # pytest, runs on host CPython
tools/             # host-side scripts (font generation, one-off device experiments)
```

**Testability rule**: anything that can be pure (parsing, filtering, formatting, layout math) goes in modules with no `machine`/`network`/`requests` imports at top level, so it runs under host CPython with pytest. Hardware and network stay in thin adapters. This is the only automated testing this project has — on-device behavior is verified by eye.

Key library choices (decided): **Microdot** (single-file asyncio web framework, vendor into `src/lib/`) for the admin panel; **peterhinch/micropython-font-to-py** `writer.py` + generated font modules for large text — framebuf's built-in 8 px font is unreadable at display distance; generate ~40–64 px fonts for departure lines. Admin panel HTML: one small page, inline CSS, no CDN/build step (device may be offline).

## SL Transport API (verified 2026-07)

- Departures: `GET https://transport.integration.sl.se/v1/sites/{siteId}/departures` — **no API key**. Query params: `transport=BUS`, `forecast=<minutes>`, `line`, `direction` (`transport`+`forecast` verified live 2026-07). Keep the JSON small on-device — a busy stop returned 45 departures at `forecast=60`; use `line`/`direction` filters and/or a smaller `forecast`.
- Each departure has `destination`, `line.designation`, `direction_code`, `stop_point`, `scheduled`/`expected` (ISO, local time, no tz offset) and **`display`** — a preformatted string ("Nu", "5 min", "12:34"). **Show `display`**; it sidesteps CET/CEST timezone math entirely (NTP gives UTC only).
- Finding the siteId: `GET /v1/sites` is ~MB-sized — fetch it on the **host** (curl/browser), never on the device. Note SL siteIds differ from the old Stop Lookup API ids. Store siteId(s) in config, editable via admin panel.
- Test from host: `curl "https://transport.integration.sl.se/v1/sites/9192/departures?transport=BUS&forecast=60"` (9192 = Slussen).
- Poll every 30–60 s. No key means shared fair-use quota — don't hammer it.
- Docs: https://www.trafiklab.se/api/our-apis/sl/transport/

## MicroPython/ESP32 gotchas

- `requests` (a.k.a. `urequests`): always `resp.close()` (in `finally:`), `gc.collect()` before each fetch, and never hold the raw response text longer than needed. HTTPS works; certs aren't validated by default (fine here).
- A JSON parse needs ~2–3× the response size in free RAM. If fetches start failing with `MemoryError`, shrink `forecast`/filter params first.
- Handle Wi-Fi drops: the fetch task should catch exceptions, keep the last good data on screen (with a stale indicator), and let a reconnect loop in `wifi.py` recover. Never let one failed request crash the program.
- Vendor dependencies as files in `src/lib/`; don't rely on `mip` at runtime.

## Working conventions for this repo

- Prefer a 5-line experiment via `mpremote run` over speculation about hardware behavior. For driver/command-sequence questions, the primary source is Waveshare's own reference implementation (link above), fetched raw — not the wiki pages, which returned 403 when checked, and not a search engine's summary of them.
- Deploy small and often; debug with serial prints. Don't refactor the panel driver once it works.
- If the panel stays blank with correct-looking code, check in this order: (1) wiring against the pin mapping above, (2) that both `0x10` and `0x13` planes are being sent, (3) BUSY polarity/the `0x71` re-poll, (4) framebuffer color convention. No DIP switch or jumper for panel variant has been found on this board's own docs so far — don't go looking for one on the strength of unrelated Waveshare panel manuals; if one turns out to exist, record it here.
- **Don't trust a driver-code question answered by summarization** (e.g. a fetch tool that runs content through a small model). For exact command bytes/sequences, get the raw source and read it directly — a paraphrase is exactly the kind of "confidently wrong" detail that put earlier claims in this file at risk (see git history of this file).

## Decisions so far

MicroPython on generic ESP32 firmware · mpremote workflow · SL Transport API (Stockholm) · Wi-Fi provisioning via AP-mode captive portal, creds stored only on device · on-device admin panel for display settings · asyncio single-loop architecture · pure-logic modules tested on host with pytest.

## Future direction (declared by owner — do NOT build yet, but don't design against it)

More content sources will join bus departures later: weather, and data from the owner's **Homey Pro** smart-home hub (on the same Wi-Fi; it exposes a local API). Practical implication today: treat "fetch data → render a screen region" as a repeatable pattern (SL is instance #1), keep the screen layout sectioned rather than hardcoded whole-screen, and remember each extra data source costs RAM — the budget in "Hardware" is the ceiling. Don't add abstraction layers for this now; just avoid baking departures-only assumptions into `display.py` and the fetch task.

## Open questions (settle with the owner, then record above)

Screen layout/design; refresh cadence vs. panel lifespan; which stops/lines (runtime config, not code); whether to use the V2 fast/partial refresh modes; power strategy (USB-powered assumed; deep sleep unexplored); admin panel feature set; when/how weather and Homey Pro sources get added (see Future direction).
