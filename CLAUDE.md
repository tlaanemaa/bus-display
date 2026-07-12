# CLAUDE.md

Guidance for Claude Code working in this repo.

## Project

DIY bus departure display: a Waveshare 7.5" e-paper panel + ESP32, MicroPython, showing real-time SL (Stockholm) departures. The spec is discovered incrementally with the owner; this file records settled decisions. **When a decision is settled, record it in the relevant section here and drop any now-answered item from "Open questions."**

You cannot see the physical screen. Verification loop: deploy → reset → watch serial → ask the owner to look. **Make the code corroborate the screen** — whenever the display is redrawn, print the same content to serial so logs alone confirm most behavior.

## Hardware (fixed facts — do not rediscover)

- **Board**: Waveshare "Universal e-Paper Driver Board" — ESP32-WROOM-32, 4 MB flash, no PSRAM. USB = COM port.
- **Panel**: Waveshare 7.5" e-Paper **V2**, 800×480, black/white, SPI, controller on panel. Full refresh ~4–5 s and flashes B/W (normal).
- **Wiring (fixed by the board, non-standard SPI pins — set explicitly, defaults fail)**: BUSY=25, RST=26, DC=27, CS=15, SCK=13, MOSI=14 (no MISO, write-only). `SPI(2, baudrate=4_000_000, sck=Pin(13), mosi=Pin(14))`. Pin mapping from the GxEPD2 Arduino reference for this exact board; hardware-confirmed.
- **BUSY is active-low, and polling the pin alone isn't enough**: busy while BUSY=0, and the driver must send command `0x71` (status read) before *each* pin read in the wait loop — without it the flag never updates and the wait hangs forever.
- **Pixel polarity** (confirmed byte-for-byte vs the reference source): at the wire, bit **0=white, 1=black**. With `framebuf.FrameBuffer(buf, 800, 480, MONO_HLSB)`: `fill(0)` = white background, draw color 1 = black — no conversion needed. Confirm with `tools/test_pattern.py` on bring-up.
- **Every full refresh writes TWO planes**: `0x10` = bitwise-NOT of the image (the controller's internal "old" plane), then `0x13` = the real image, then `0x12` triggers. `0x13` alone is not enough.
- **Driver is ported** — `src/epd7in5v2.py`, a line-for-line MicroPython port of Waveshare's `epd7in5_V2.py` (RaspberryPi_JetsonNano/python/lib/waveshare_epd/ at https://github.com/waveshareteam/e-Paper, fetched 2026-07-04): same command bytes/order and `0x71` polling. **Don't re-derive the init sequence** — if the screen misbehaves it's wiring, framebuffer content, or call order, not the command bytes. `init_part`/partial ARE ported, as a *differential* update (see "Screen refresh strategy"). `init_fast`/4-gray intentionally not ported. Confirmed working on hardware 2026-07-04.
- **No ready-made MicroPython driver exists for this panel+board — don't re-search.** Waveshare's repo has no MicroPython folder; the common community port (mcauser/micropython-waveshare-epaper) has no V2 variant. The port here IS the "use Waveshare's solution" path.
- **RAM budget: ~165,632 bytes free heap** right after boot (`gc.collect(); gc.mem_free()`), MicroPython v1.28.0 ESP32_GENERIC, before app code.
- **The 48 KB framebuffer is allocated ONCE at boot and kept resident** (`display_loop` allocates it, `_draw_and_refresh` reuses it). This reverses an earlier rule — see "RAM-vs-HTTPS conflict (RESOLVED)". The transient-per-cycle pattern existed *only* because a resident buffer starved the SL **TLS** handshake; now that SL + Open-Meteo are fetched over plain **HTTP** (no handshake), that pressure is gone, and a resident buffer is also *more* robust: a fresh 48 KB alloc had started `MemoryError`-ing on later cycles once fetches became reliable, because the heap fragments and MicroPython's GC never compacts. Allocating once, on the clean boot heap (~90 KB contiguous), sidesteps that. The only contiguous demands left are that one boot alloc and the tiny JSON parses (SL responses are ~2–4 KB here).
- **Never hold two 48 KB (BUF_SIZE) buffers at once — the allocator doesn't defragment.** A second full buffer `MemoryError`s (the heap fragments and nothing compacts). The resident buffer serves both differential planes by re-rendering into itself (see "Screen refresh strategy"); driver writes stream through a small reusable 512-byte scratch (`_write_bulk_inverted`/`_write_fill`), never a second full buffer. Apply the same pattern to any framebuffer-transforming code.

## Physical mounting & drawable area

Panel mounted rotated 90° (portrait) inside a picture frame that crops the edges unevenly. All content goes through a 90° transform from a logical portrait canvas into the native 800×480 buffer, and stays inside a calibrated safe sub-rectangle.

- Logical canvas: 480 wide (`LW`) × 800 tall (`LH`), swapped from native 800×480.
- Transform: logical `(lx, ly)` → physical `(WIDTH-1-ly, lx)`, `WIDTH=800`. Rect: `fill_rect(WIDTH-ly0-lh, lx0, lh, lw)`. Framebuf can't draw rotated text — render into a small scratch buffer, then blit its set pixels through the transform (`display.py:_text()`). Confirmed on hardware.
- **Safe margins** (calibrated with `tools/calibration_guide.py`): left=7, top=33, right=0, bottom=43 px within the 480×800 canvas. Right=0 because that edge had almost no crop.
- **Never draw a visible border/outline** around the drawable area — the owner finds it makes any misalignment obvious. The margins are silent layout bounds, not a rendered frame.

## E-paper rules (breaking these damages the panel or wastes hours)

1. Call `sleep()` after every refresh — leaving the panel powered/active degrades it. `_draw_and_refresh` puts everything from `init()`/`init_part()` onward inside a `try:` whose `finally:` calls `_safe_sleep(epd)`, so even a write/busy-wait error mid-refresh still powers the panel down (`_safe_sleep` swallows its own error so it can't mask the original, and a hang inside it is the WDT's job). Don't drop this guard back to a bare trailing `epd.sleep()`.
2. Refresh only when rendered content changed (compare rendered text, not raw API responses — `expected` timestamps jitter). Waveshare recommends ≥180 s between *full* refreshes; the owner tunes this. Most per-minute refreshes are **partial** (see "Screen refresh strategy"), with a periodic full to clear ghosting.
3. Init → write buffer → display/turn-on → sleep, every cycle. After `sleep()`, re-init before the next refresh.
4. The refresh is a blocking busy-wait (~5 s). Acceptable — don't make the SPI driver async.

## Toolchain & commands

Host tools: `pip install esptool mpremote pytest mpy-cross` (mpy-cross precompiles vendored libs — see gotchas).

Find the port: `mpremote connect list` (called COM3 below).

One-time flash (download ESP32_GENERIC .bin from micropython.org/download/ESP32_GENERIC — plain 4 MB variant, not SPIRAM/OTA):

```
esptool --port COM3 erase-flash
esptool --port COM3 --baud 460800 write-flash 0x1000 ESP32_GENERIC-<version>.bin
```

(esptool v5 prefers hyphenated `erase-flash`/`write-flash`. If it won't connect, hold the board's BOOT button while it retries.)

Everyday loop:

```
deploy.bat                                         # Windows: mpy-cross-compile every module + copy .mpy/main.py/json/fonts + reset
mpremote connect COM3 fs cp src/main.py :main.py   # main.py only -- it ships as source; changed alone, cp it directly
mpremote connect COM3 reset                        # restart so new code runs (cp does NOT restart)
mpremote connect COM3 repl                         # serial console; Ctrl-] exits, Ctrl-C interrupts main.py
mpremote connect COM3 run tools/foo.py             # run a host file on-device WITHOUT copying (hardware experiments)
pytest                                             # host-side tests for the pure-logic modules
```

**The whole app ships as precompiled `.mpy`, not source** (`deploy.bat` runs `mpy-cross` on every `.py`, then copies only the bytecode). This keeps the boot heap clean and unfragmented on this PSRAM-less board: on-device compilation spikes/fragments RAM (parser/AST), which now threatens the resident 48 KB framebuffer's one-time boot allocation (and historically starved the TLS fetch — see "RAM-vs-HTTPS conflict (RESOLVED)"). `main.py` is the ONE exception (MicroPython auto-runs `:main.py` by name; it's small enough to compile on-device with everything else precompiled). So a single-file quick-deploy works directly only for `main.py`; for any other module, recompile first (`python -m mpy_cross src/foo.py && mpremote connect COM3 fs cp src/foo.mpy :foo.mpy`) or just run `deploy.bat`. The `.mpy` are **gitignored build artifacts** — `.py` is the source of truth. Requires `pip install mpy-cross`.

Only one process can hold the COM port — close any open REPL before deploying. `main.py` auto-runs on boot; keep it Ctrl-C-recoverable (catch exceptions, print, idle — never a tight reset loop, or the board becomes hard to reflash).

## Architecture

Single asyncio event loop, no threads. The admin server and the fetch/redraw loop are **not both running** once connected (see "Departures logic & stops").

Boot flow: load `/config.json` → try Wi-Fi STA (~15 s timeout) → **success**: NTP sync, brief settle, warm fonts, allocate the resident framebuffer, straight into the fetch/redraw loop (no "booted" screen, no admin server); **failure**: AP mode (SSID `BusDisplay-Setup`, portal `http://192.168.4.1`) serving a Wi-Fi form → save to `/config.json` → reboot. Wi-Fi creds live only on the device.

**NTP is re-synced periodically, not only at boot** (`display_loop`, daily wall-clock bucket, added 2026-07-12): the boot `ntptime.settime()` is caught-and-ignored on failure, and the ESP32 RTC drifts over long 24/7 uptime — so a resync loop keeps the footer clock honest and self-heals a failed boot sync (`last_ntp_bucket` starts `None` → first tick resyncs; failures don't advance the bucket, so it retries each tick, bounded by ntptime's ~1 s socket timeout, until it succeeds). Departure countdowns are server-computed by SL so they're unaffected by RTC drift; only the footer clock/date and tick alignment depend on it.

```
src/                 # maps 1:1 to the device filesystem root
  main.py            # boot flow, resident framebuffer alloc, asyncio loop
  config.py          # load/save /config.json (Wi-Fi creds)
  settings.py        # load /settings.json (stops, cadence — gitignored)
  wifi.py            # STA connect w/ timeout, AP fallback
  server.py          # Microdot admin app (AP-mode Wi-Fi setup only)
  display.py         # layout/render onto the framebuf; logs what it drew
  bitfont.py         # streamed 1-bit font reader (deployed as bitfont.mpy)
  fonts/             # *.fnt bitmap fonts streamed from flash (gen_font.py)
  epd7in5v2.py       # panel driver (Waveshare port, pins above)
  sl.py              # thin HTTP wrapper: fetch departures JSON (plain http, no TLS)
  departures.py      # PURE parse/filter/format — no hardware imports
  openmeteo.py       # thin HTTP wrapper: fetch Open-Meteo forecast JSON
  weather.py         # PURE: forecast JSON -> footer summary + glyph bucket
  localtime.py       # PURE UTC->Stockholm CET/CEST converter
  lib/               # vendored (microdot.mpy) — don't hand-edit
tests/               # pytest on host CPython
tools/               # host-side scripts (bring-up, one-off experiments)
                     #   gen_font.py: TTF -> .fnt; diag_mem.py: RAM probe;
                     #   preview_home.py / preview_weather.py: host PNG previews
```

**Testability rule**: anything pure (parsing, filtering, formatting, layout/time math) goes in modules with no `machine`/`network`/`requests` top-level imports, so it runs under host pytest. Hardware and network stay in thin adapters. On-device behavior is verified by eye — this is the only automated testing.

**Key library choices**: **Microdot** (single-file asyncio web framework, in `src/lib/`) for the admin panel — runs **only during AP-mode setup**, not once connected. **Fonts**: a custom **streamed bitmap font** (`bitfont.py` + `fonts/*.fnt`, see "Fonts" below) replaced framebuf's built-in 8 px font. **peterhinch/micropython-font-to-py is the wrong tool here** and was tried+reverted (2026-07-11): it emits a *resident* Python glyph module, and even ~15 KB resident competes with the resident 48 KB framebuffer and other app RAM on this PSRAM-less board. The streamed approach keeps glyphs on flash instead.

### Fonts (streamed bitmap — settled 2026-07-12)

Print-like **Bitter** (slab serif; robust at 1-bit, strong hero digits) rendered on the host into compact 1-bit `.fnt` files and **streamed from flash one glyph at a time** — never resident. This is what makes a smooth font viable on this PSRAM-less board. The panel is 1-bit (no anti-aliasing), so smoothness comes purely from rendering glyphs at their real pixel size, not scaling an 8×8 cell.

- **Three sizes** (fixed px, not the old arbitrary scale): `bitter_hero.fnt` (~87 px countdown, weight 800), `bitter_head.fnt` (~35 px labels/headline, 700), `bitter_row.fnt` (~27 px rows/footer, 500). Total ~26 KB flash, **zero resident glyph data**.
- **Format + regen**: `tools/gen_font.py` (host, Pillow) renders `tools/fonts/Bitter-var.ttf` → `src/fonts/*.fnt` (advance-width cells, on-disk index, ink-cropped; format documented in that file). `bitfont.py` is its exact reader. Charset = printable ASCII **+ `°` (U+00B0)** for the weather temps (head/row fonts; hero stays digits-only) — Swedish is transliterated to ASCII upstream by `_to_ascii`. Any new non-ASCII glyph must also be added to `display.warm_fonts()`, or it allocates mid-draw (see RAM discipline). Deploy `bitfont.mpy` (precompiled) so import doesn't compile-fragment the heap.
- **Glyphs stream from flash, never resident** — the core reason a nicer font is viable at all: a *resident* glyph module (peterhinch/font_to_py, ~15 KB) competes with the resident 48 KB framebuffer and other app RAM, and crashed the loop; streaming keeps only ~one glyph in RAM at a time. That decision stands.
- **The stricter no-per-draw-allocation discipline (bitfont.py) is no longer load-bearing, but is kept.** It was a *second* front of the old RAM-vs-HTTPS fight: while the SL **TLS** handshake needed a large contiguous block, any heap churn during a draw could strand an object and starve the next fetch (`MBEDTLS_ERR_MPI_ALLOC_FAILED`). With SL over plain **HTTP** now (no handshake) and only tiny JSON parses needing contiguity, that risk is gone. The code still does it (one reused module-level scratch buffer via `readinto`; a module-level `plot` function + inlined transform; `display.warm_fonts()` pre-warms advance caches at boot) — it's cheap, correct hygiene, so **don't reintroduce per-draw allocations without reason**, but it's no longer the razor's edge it once was.

## SL Transport API (verified 2026-07)

- Departures: `GET http://transport.integration.sl.se/v1/sites/{siteId}/departures` — **no API key**. Params: `transport=BUS`, `forecast=<minutes>`, `line`, `direction`. Keep the JSON small on-device (a busy stop returned 45 departures at `forecast=60`).
- **Plain HTTP, deliberately** (`sl.py`, settled 2026-07-12): the endpoint serves over `http://` with no redirect to `https`, and using it **skips the TLS handshake entirely** — which was the whole "RAM-vs-HTTPS conflict" on this board (mbedtls's RSA-2048 cert verification intermittently ran out of contiguous heap and hung/failed the fetch). It's public transit data with no key or credentials, so there's nothing to protect by encrypting it. This was THE fix that made departures reliable on-device.
- Each departure has `destination`, `line.designation`, `direction_code`, `scheduled`/`expected` (ISO local time, no tz offset), and **`display`** — a preformatted string ("Nu", "5 min", "12:34"). **Show `display`** — it sidesteps CET/CEST math (NTP gives UTC only).
- siteId: `GET /v1/sites` is ~MB-sized — fetch on the **host** (curl/browser), never on the device. SL siteIds differ from the old Stop Lookup API ids.
- Host test: `curl "http://transport.integration.sl.se/v1/sites/9192/departures?transport=BUS&forecast=60"` (9192 = Slussen).
- No key = shared fair-use quota — poll ~1×/min, don't hammer. Docs: https://www.trafiklab.se/api/our-apis/sl/transport/

## Weather API (Open-Meteo — settled 2026-07-12)

- Today's forecast: `GET http://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=1` — **no API key**, like SL. `openmeteo.py` fetches, `weather.py` parses (pure).
- **Plain HTTP**, like SL (verified no https redirect 2026-07-12): skips the TLS handshake, so the weather fetch can't reopen the mbedtls contiguous-RAM starvation either. Public data, no key.
- **Open-Meteo, not SMHI, on purpose**: SMHI's point forecast returns the whole multi-day hourly series (100 KB+); Open-Meteo lets us request **only today's daily fields → ~1 KB**. `timezone=auto` so the daily min/max aggregate over the LOCAL day.
- **WMO `weather_code` → glyph bucket** in `weather.condition_for_code()`: clear/partly/cloudy/fog, **drizzle/rain/rain_heavy** (rain split three ways — intensity is the umbrella-decision axis, and WMO carries it: 51/53/55 drizzle, 61/63/80/81 rain, 65/82/99 heavy), snow, thunder. Unknown → cloudy.
- Coords + cadence live in `/settings.json`'s optional `weather` block (see settings.example.json); absent/`enabled:false` → the footer draws the clock only. Pulled on its own slow bucket (`pull_interval_min`, default 30 — weather changes slowly, be gentle on the keyless quota), independent of the departures pull.
- **Fetch-failure policy (refined 2026-07-13): keep a still-valid last-good reading; show "Weather error" only when nothing usable.** Weather is a *daily* forecast, so a recent last-good reading is more useful than an error during a fetch outage (owner's call). BUT it isn't static — **Open-Meteo revises the daily forecast through the day** as new model runs arrive (verified against the docs: DWD ICON re-runs ~every 3 h, GFS/Met-France hourly; precip probability especially can swing). So "keep last-good" is bounded on **two** axes, both in the pure `weather.keep_last_good(reading, today_iso, age_s, max_age_s)`:
  1. **Same local day** — `reading["date"]` (carried from Open-Meteo `daily.time[0]` by `parse_weather`) must equal `_local_today_iso()`. Catches the across-midnight case.
  2. **Fresh enough** — fetched no more than `max_age_s` ago (`weather.max_age_min`, **default 180 = 3 h**, ~one ICON cycle; configurable in the weather block). Bounds how stale the *outage fallback* can get, since the forecast revises during the day. `main.py` tracks `last_weather_time` (`time.time()` of the last successful pull) and passes `age_s = now - last_weather_time`.

  On a failed/unusable pull, `display_loop` keeps `last_weather` (clears `weather_error`) iff `keep_last_good` is true; otherwise it sets the explicit **"Weather error"** (`display.WEATHER_ERROR`). This replaced the earlier always-error-on-failure rule (which flashed the error over one transient bad payload even with a good reading in hand). **Retry cadence**: the every-render-tick retry fires **only while `weather_error` is actually set** (nothing usable → clear it fast); while showing a valid last-good reading there's no urgency, so it waits out the normal (up to 30 min) bucket — meaning the flip from "kept reading" to "error" happens at the first bucket after `age_s` crosses `max_age_s` (so the reading can show up to ~`max_age + one bucket` old, close enough). The honest-error reasoning still holds for the stale case — same as the per-stop STALE badge, don't show untrustworthy data next to a live clock unmarked.

## Departures logic & stops

- **Config in `/settings.json`, gitignored** (stop ids, direction, forecast, cadence) so the owner's home stop isn't in a public repo. `settings.py` loads it (mirrors `config.py`). `src/settings.example.json` is the committed template (uses the public Slussen siteId as a placeholder). Edit + deploy it like any file — no `main.py` change for settings-only edits. JSON not YAML (MicroPython has `json` built in, no YAML parser).
- **Stops**: a flat, ordered list of `{name, site_id}`; every stop is always fetched and shown in order (no primary/fallback/suitability logic). Names render on-screen as section labels.
- **`departures_per_stop`** (default 3): next N departures per stop. **`forecast_min`** default **180** (3 h) — SL's `forecast` caps at 1200 min (20 h; 400 Bad Request past that), and a busy hub returns ~31 KB there (RAM risk: a JSON parse needs ~2–3× its size). Raise it only if a sparse-service stop comes up empty.
- **Direction filter**: `direction_code=2` = towards Gustavsberg/city (`1` = the other way). Filtered server-side via the `direction` param to keep responses small.
- **Fetch strategy**: each stop is fetched independently every cycle (`main.py:_fetch_all_stops`); one stop failing keeps its last-good departures and flags **that stop** stale (a per-stop STALE badge, see "Screen design"), without touching the others. `stale_flags = [r is None for r in results]` — tied to whether the fetch **errored**, not to the data shape. If *every* stop errors and none has prior data, the screen still renders ("No departures" + STALE everywhere) rather than freezing silently.
- **The render gate is `have_fetched`, NOT `any(last_good)`** (fixed 2026-07-12): rendering is skipped only until the first pull attempt *completes*, tracked by an explicit `have_fetched` flag. It must **not** be inferred from "last_good is empty" — a successful pull returning zero departures (a normal nighttime state for a sparse stop within `forecast_min`) also leaves `last_good` empty, and that case must fall through to render "No departures". The old `not any(last_good) and not any(stale_flags)` gate conflated the two, so when every stop was legitimately empty the panel silently kept showing the previous evening's departures with no STALE badge — the exact stale-mistaken-for-current failure the badges exist to prevent.
- **Sort by `expected`, not `scheduled`**: a delayed bus's scheduled time can precede an on-time bus's, wrongly ordering them. `expected` matches what `display` shows. Regression test: `test_parse_departures_sorts_by_expected_not_scheduled_when_delayed`.
- **Refresh cadence — three intervals, in MINUTES** (`main.py` converts to seconds internally):
  - `data_pull_interval_min` (1) — how often to fetch fresh departures from SL.
  - `render_interval_min` (1) — loop tick: re-render from cached departures + the current clock, push a refresh *only if the rendered text changed*. This is the refresh floor (can't push faster than you tick).
  - `full_refresh_interval_min` (code default 30; owner's `settings.json` set to **60** for gentler 24/7 wear — the differential partial ghosts little in practice, so hourly full clears are enough and halve the nighttime B/W flashes) — how often a push is a full (flashing) refresh vs a differential partial.

  Pull and render are separate knobs (pull less often than you render if you want a live clock but gentler API use).
- **Ticks aligned to the WALL CLOCK, landing on `HH:MM:00`** so the on-screen clock flips in step with a phone. `_seconds_to_next_tick()` sleeps onto the next interval multiple using the NTP-synced `time.time()`. **Never wakes early** — the property that matters, since early = rendering the old minute, a full minute behind. `int(time.time())` floors, so the computed sleep overshoots the boundary by the sub-second fraction → we wake 0–1 s after `HH:MM:00`. (An earlier fixed `+2 s` margin was removed as premature — it visibly lagged the clock behind a phone.) Data pulls use an integer `time.time() // interval` bucket so they fire once per interval regardless of jitter. A long tick (worst case ~90 s) just lands on a later boundary; no drift accumulates.
- **Watchdog fed during idle waits**: `_sleep_until_next_tick()` feeds the WDT every 60 s while sleeping, so a `render_interval_min` larger than the 150 s WDT window won't trip a spurious reboot mid-wait. The ESP32 stays awake between ticks (Wi-Fi up, RAM live); only the **panel** deep-sleeps (`epd.sleep()`). `machine.deepsleep` is deliberately unused — it would wipe `prev_frame` (the differential's old-plane source) and the resident framebuffer, forcing a full flash every wake (see "Open questions", power).
- **Swedish characters (å/ä/ö/é/ü)** are transliterated to plain ASCII by `departures.parse_departures()` via `_to_ascii`. Historically forced by framebuf's ASCII-only font; with the streamed bitmap font (see "Fonts") native å/ä/ö glyphs are now feasible almost for free (add them to `gen_font.py`'s charset + stop transliterating), but not yet done — a clean follow-up.

### Screen design

**Kitchen-counter, glance-from-across-the-room display, not a reading surface.** The one fact that matters is "how soon do I need to leave" — everything below follows from that, not from generic "make text bigger."

- **Per-stop sections**, each labeled with the stop name (`display.stop_section()`) and its own rule under the label (`RULE_HEIGHT`). Sections are separated from each other by whitespace only (`GROUP_GAP=48`) — an inter-section divider line was tried and **removed per owner feedback** (2026-07-12): it read as visual clutter once the gap itself did the separating job. `GROUP_GAP` is tuned so that gap visually matches the content-to-footer gap in the worst-case (both stops at max departures) layout — the two whitespace rhythms read as the same size.
- **Hero + caption**: each stop's soonest departure's `display` string is drawn alone, huge (`HERO_SCALE=7`, centered) — it answers the one question at a glance. A caption under it (`CAPTION_SCALE=2`, centered, `<line>  <destination>`, no padding — padding would break the centering) names the bus. Remaining departures are a compact left-aligned list (`ROW_SCALE=2`, 3-column line/destination/display).
- **Footer = one status line** (`display._draw_footer_status_line`, settled 2026-07-12): weather cluster **left**, date/time **right** — mirroring the departure rows' info-left / time-right rhythm. All row-size (the footer is secondary to the hero countdowns). This replaced a stacked weather-row-over-clock (too tall; only ~13 px of separation in the busy 3+3 case) — and an intermediate **divider-rule hack was rejected by the owner** (a horizontal line near the frame cutout highlights any misalignment; same reasoning as the banned border). Collapsing to one line freed ~45 px, which *is* the separation. Local time from NTP UTC via `localtime.py`, a pure CET/CEST converter (Sakamoto day-of-week, hand-rolled calendar rollover; avoids `datetime`/`time.localtime` for host/device portability; tests in `tests/test_localtime.py`).
  - **Weather cluster**: condition **glyph** + high/low (`6° / 12°`, low first — the jacket number) + a droplet + rain chance, shown only when precip ≥ `weather.PRECIP_SHOW_THRESHOLD` (10 %, lowered from 20 % 2026-07-12 — the icon already says "dry" below that; also auto-dropped if it would crowd the date). Answers "umbrella? jacket?" at a glance. Glyphs are **procedural 1-bit icons** drawn through the mount transform, integer-math + `_plot_run` only (allocation-free); dispatched by condition string in `display._WEATHER_DRAWERS` (keys match `weather.py`). Preview on host with `tools/preview_weather.py`.
  - **Weather fetch failure = "Weather error" text ONLY when nothing valid for today** (`display.WEATHER_ERROR` sentinel; refined 2026-07-13), replacing the whole weather cluster in the footer band. A last-good reading that's still **today's** forecast is kept and shown instead (a daily high/low a few hours old is still current-for-today); the explicit error appears only with no last-good or a **prior-day** one — see "Weather API → Fetch-failure policy" for the `date`-match rule. The honest-error reasoning (don't show genuinely-stale, prior-day data next to a live clock unmarked — same as the per-stop STALE badge) still governs that fallback. `main.py` decides `weather_error` per pull from the fetch outcome + the kept reading's `date`.
- **Stale marker = per-stop STALE badge** (`display.draw_home`, after the stop name; settled 2026-07-12): when a stop's fetch **errored**, an inverted "STALE" pill (same badge language as the line numbers) sits right on that stop's header line, next to the countdown it's warning about — as prominent as the data, and per-stop (one stop failing doesn't mark the others). Deliberately NOT a footer suffix (missed) and NOT a top banner (a bar at the top edge reads as a border near the cutout — see the anti-border rule). `stale_flags` is error-driven (see "Departures logic").
- **Content margin**: `CONTENT_MARGIN=25` px (~5 mm) inset within the calibrated safe rectangle, on all four sides except the footer's bottom (`display.py`'s `CONTENT_X0/Y0/W/H`). A styling choice, separate from the hardware crop margins.
- **Set aside**: merging all stops into one time-sorted feed (rejected — owner wants clear per-stop sections); mixed font scales within the hero line (not built, marginal gain).

### Screen refresh strategy — differential partial refresh

Partial refresh on the 7.5" V2 is a **differential** update and must be driven as one. Running in production; owner reports the refresh solid.

- **Differential**: the controller drives each pixel from its `0x10` "old image" plane to its `0x13` "new image" plane with a gentle no-flash waveform. Stock `display_Partial()` sends **only** `0x13`, leaving `0x10` stale → pixels driven against a wrong reference → heavy ghosting (the original root cause). Fix (per `betterepd7in5`: https://github.com/hchargois/betterepd7in5, writeup https://thoughts.gohu.org/posts/2025/epaper-partial-updates/): send the **actual previous frame on `0x10`** and the new frame on `0x13`. Only changed pixels move — and only the hero countdown + footer clock change each minute, so ghosting is minimal.
- **Driver API** (`epd7in5v2.py`): `partial_begin()` (`0x50`/`0xA9`/`0x07`, `0x91` enter partial, `0x90` full-frame window) → `partial_old(buf)` (`0x10` + old frame) → `partial_new(buf)` (`0x13` + new frame, then `0x12` refresh). Split so **the one (resident) 48 KB buffer serves both planes** (render old frame → stream to `0x10` → re-render the new frame into the SAME buffer → stream to `0x13`; the `0x10` bytes already live in the controller by then). Keeps the "never two BUF_SIZE buffers" rule — **do not** add a two-buffer `display_partial_diff`. Full-frame window, not cropped.
- **Plane polarity**: both `partial_old`/`partial_new` currently invert (`_write_bulk_inverted`), matching the confirmed-legible full `display()`. **If changed pixels ever ghost/darken instead of resolving cleanly, flip `partial_old` to the non-inverted `_write_bulk`** — that one line is the whole fix.
- **Panel sleeps after every refresh** (e-paper rule 1) — the differential re-uploads the old plane explicitly, so it no longer depends on controller RAM surviving between calls. `init_part()` → `partial_begin/old/new` → `sleep()` each time.
- **Mode**: full when `full_refresh_interval_min` (owner's config 60; code default 30) has elapsed since the last full, or on the first refresh (no previous frame to differential against); else the differential partial. Gated on `content_changed` — ghosting only accrues while redrawing, so there's nothing to clear if nothing changed.

### RAM-vs-HTTPS conflict (RESOLVED 2026-07-12 — kept for history)

For most of this project the fetch was the enemy: **SL's TLS handshake** (mbedtls verifying its RSA-2048 cert) needs a large *contiguous* heap block, and on this PSRAM-less board resident RAM starved it → intermittent hangs and `MBEDTLS_ERR_MPI_ALLOC_FAILED`, surfacing as "the data goes stale." It drove nearly every RAM decision here (transient framebuffer, lazy `server` import, `.mpy`-everything, streamed fonts, no per-draw allocation).

**The fix that dissolved it: fetch over plain HTTP, not HTTPS.** SL and Open-Meteo both serve `http://` with no redirect — no handshake, no mbedtls, no contiguous-block demand. Public data, no keys, nothing to protect. Departures became reliable on-device immediately.

How it was cracked (the diagnostic path, worth repeating for the next hard RAM bug): (1) SL answered the **host** in 0.1 s → not SL/network, device-side. (2) On-device it failed with ~106 KB free but the failure was `MPI_ALLOC_FAILED` — **contiguous free is the metric, not `gc.mem_free()`** (`micropython.mem_info()` → "max new split"). (3) Disabling **weather** didn't fix it → not that feature specifically. (4) Open-Meteo's *lighter* handshake succeeded while SL's failed → SL's cert/handshake was the heavy one. Then: does SL even need TLS? No.

Consequences now baked in:
- **Framebuffer is resident** (allocated once at boot). It was transient *only* to free room for the TLS handshake; with TLS gone that's unnecessary, and resident also fixed a *second*, previously-masked `MemoryError` — a fresh 48 KB alloc had begun failing on later cycles from heap fragmentation, but the TLS failures were stopping the device before it ran two cycles, hiding it.
- **Lazy `import server`** (inside the AP-mode branch only) stays. Its original point — `server.py` builds its whole Microdot app at import time (~30 KB+) — is a general lesson that outlives TLS: **an unused import isn't free if its top-level code does real work.**
- **The WDT (`machine.WDT(150 s)`, fed each `display_loop` iteration) stays** as the general hang backstop — force-reboots on any hang, Python-catchable or not. It should now rarely fire (no TLS to hang); if it starts firing every boot, that's a regression — check for a new eager unconditional import or a resident allocation that broke the boot-time framebuffer alloc.

## MicroPython/ESP32 gotchas

- `requests`/`urequests`: always `resp.close()` in `finally:`, `gc.collect()` before each fetch. Fetches are plain **HTTP** now (see "RAM-vs-HTTPS conflict (RESOLVED)") — no TLS. If you ever reintroduce an HTTPS endpoint, expect the contiguous-RAM fight back and prefer an `http://` alternative first.
- **Shared retry/timeout policy** (`sl.py` + `openmeteo.py`, settled 2026-07-12): `retries=3`, `timeout_s=10` (aggressive on purpose — a request that hasn't returned in 10s is treated as dead and retried, not waited out), `RETRY_DELAY_S=3` between attempts (not after the last one). Worst case for one fetch: `3*10 + 2*3 = 36s`. Keep the two modules' defaults in sync (see main.py's `WDT_TIMEOUT_MS` comment for why the numbers matter — they're the whole per-tick time budget).
- A JSON parse needs ~2–3× the response size in free RAM. If fetches start failing with `MemoryError`, shrink `forecast`/filters first.
- Handle Wi-Fi drops: catch exceptions, keep the last-good data on screen with a stale flag, let a reconnect recover. Never let one failed request crash the program. **Explicit reconnect** (`wifi.reconnect`, added 2026-07-12): `display_loop` counts consecutive pulls in which *every* stop errored (`all(stale_flags)` — a connectivity signal, not an SL-side one) and after `_WIFI_RECONNECT_AFTER_FAILS` (3) forces a `wifi.reconnect(ssid, pw)` (toggles the STA off/on, then re-runs `connect_sta`). The ESP32 usually auto-reconnects on its own; this is the belt-and-suspenders for a router power-cycle (the likeliest 24/7 outage) where auto-reconnect can wedge. The counter resets on any success or after an attempt, so at most one reconnect per 3 failed pulls — never a hammer. Wi-Fi creds reach the loop via `display_loop(cfg, wifi_cfg)` (threaded from `config.load()` in `main()`).
- **The whole app ships as precompiled `.mpy` (`deploy.bat` compiles every `.py` but `main.py`), because on-device compilation spikes/fragments the heap.** First seen vendoring `microdot.py` (58 KB): compiling it *on device* threw `OSError: WiFi Out of Memory` (parser/AST spikes transient RAM, fragmenting the heap so the Wi-Fi driver's alloc fails). The fragmentation metric is **largest contiguous free block**, not `gc.mem_free()` (`micropython.mem_info()` → "max new split" / "max free sz"): compiling on-device once collapsed contiguous free to ~7 KB. Precompiling everything keeps the boot heap clean (~90 KB contiguous) — which the resident 48 KB framebuffer's one-time alloc needs. (Historically this also unblocked the TLS fetch, now moot with HTTP — see "RAM-vs-HTTPS conflict (RESOLVED)".) So: compile on the host, never the device. PyPI's `mpy-cross` (1.27.0) lags the firmware (1.28.0) but the bytecode matches as of 2026-07 — if a future `.mpy` fails to import with a version error, check this first.

## Working conventions

- Prefer a 5-line `mpremote run` experiment over speculating about hardware behavior.
- For driver/command-byte questions, read Waveshare's raw reference source directly — not the wiki pages (they 403'd), and **not a model's summary of them** (paraphrased command bytes are exactly the "confidently wrong" detail to avoid).
- Deploy small and often; debug with serial prints. Don't refactor the panel driver once it works.
- **Blank panel with correct-looking code** — check in order: (1) wiring vs the pin map, (2) both `0x10` and `0x13` planes sent, (3) BUSY polarity / the `0x71` re-poll, (4) framebuffer color convention. No panel-variant DIP/jumper exists on this board — don't hunt for one.

## Future direction (declared by owner — do NOT build yet, but don't design against it)

Weather is now built (footer overview — see "Weather API" and "Screen design"), the **second** data source after SL and proof the "fetch data → render a screen region" pattern holds. Still to come: the owner's **Homey Pro** hub (same Wi-Fi, local API). Keep `display.py` sectioned rather than whole-screen-hardcoded, and remember each source costs RAM (the Hardware budget is the ceiling) and a slice of the WDT fetch budget. Don't add abstraction layers yet — just don't bake departures-only assumptions into `display.py`/the fetch task.

## Open questions

- Is the streamed Bitter font legible/comfortable at real viewing distance (host preview looks good; owner to eyeball on the panel)? Are the three chosen sizes right?
- Does the ~5 mm content margin read comfortably in person (currently reasoned from an approximate dot pitch)?
- Is ~1×/min panel refresh the right wear/freshness trade-off, or should it relax further? `full_refresh_interval_min` was relaxed 30 → 60 for 24/7 wear (2026-07-12); the every-minute *partial* (driven by the live footer clock) is still the main wear/flash driver — an even gentler render cadence (clock every 2–3 min) remains an option if longevity beats phone-synced clock feel.
- If ghosting ever appears on the partial refresh, flip `partial_old` polarity (see "Screen refresh strategy").
- Power: USB assumed; ESP32 deep sleep set aside (would wipe `prev_frame` and the resident framebuffer, forcing a full flash every wake — only worth it on battery). The panel already deep-sleeps between refreshes.
- Does the admin panel return once connected in STA mode, and scoped to what (maybe just Wi-Fi re-provisioning)?
- **Weather (new 2026-07-12)**: are the procedural glyphs legible on the panel at viewing distance (host preview looks good; owner to eyeball)? Is the three-way rain split the right resolution, or is drizzle-vs-rain-vs-heavy overkill? Is `pull_interval_min=30` right? Owner must add real `latitude`/`longitude` to `/settings.json`'s `weather` block (example uses Stockholm 59.33/18.06).
- When/how the Homey Pro source gets added (see Future direction).
