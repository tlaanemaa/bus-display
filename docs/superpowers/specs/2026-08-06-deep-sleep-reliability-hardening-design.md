# Deep-Sleep Reliability Hardening Design

## Objective

Make the existing deep-sleep runtime reliable enough for an
unattended household display without merging the broad
`codex/deep-sleep-cleanup` refactor.

Work happens on `codex/deep-sleep-reliability`, created directly from `main`.
The cleanup branch is reference material only. The continuously-awake fallback,
setup code, display architecture, configuration format, one-minute cadence, and
verified panel protocol remain in place.

The product requirement is not merely that the firmware usually updates. With
valid local configuration, a failed operational wake must either show an
honest stale/error state or automatically try again on a bounded schedule. It
must not remain indefinitely on an apparently current screen. An explicitly
malformed local configuration remains a USB-recoverable operator error with a
precise serial diagnostic; backward compatibility prevents normal deployment
from creating that condition.

## Evidence from the current implementation

The original deep-sleep commit had two concrete failure mechanisms consistent
with the reported frozen display:

1. Its RTC verification compared a live frame containing tuples with the same
   frame decoded from JSON as lists. A successful panel update could therefore
   raise during retained-state verification and fall into fatal recovery.
2. Each source could make three sequential network attempts before rendering.
   A slow socket or weather outage could consume the watchdog window and reset
   the board before the panel update or next sleep was scheduled.

Commit `822b41f` corrected those two defects on `main`, but the current code
still has production reliability gaps:

- Wi-Fi association and cold-boot NTP run before watchdog protection.
- The EPD object and Wi-Fi stack are initialized before the one 48 KB
  framebuffer is reserved, increasing fragmentation risk.
- Retained state is checked only by envelope checksum and version; malformed
  nested frames and settings-incompatible frames can reach rendering.
- RTC state is committed after the physical refresh without first invalidating
  the old state. A reset between those actions can leave RTC and panel images
  disagreeing, making the next differential refresh unsafe.
- SL and Open-Meteo adapters accept non-2xx JSON bodies and insufficiently
  validate response shape, allowing failures to masquerade as fresh data.
- Unexpected deep-sleep-path exceptions fall into an idle loop that appears
  frozen until a watchdog happens to reset it.
- Retained timestamps in the future can suppress weather, NTP, or periodic
  full-refresh work after a clock correction.
- Deployment copies the activating `main.py` before all supporting bytecode,
  leaving a mixed firmware if USB copying fails partway through.

Hardware acceptance on 2026-08-06 later disproved the plan's initial
framebuffer-before-radio remedy: reserving 48 KB first left only 4 of the 10
expected Wi-Fi RX buffers, logged deinitialization error `0x3001`, and raised
`OSError: WiFi Out of Memory`. The required deep-sleep boot order is therefore
WDT -> Wi-Fi STA initialization -> one framebuffer -> RTC/NTP/API/render/EPD.

A deployment of that corrected order exposed the other half of the allocator
constraint: serial reached `wifi: connected ...`, then `_allocate_framebuffer`
raised `MemoryError: memory allocation failed, allocating 48000 bytes`. The
41.9 KB source `main.py` had been compiled on-device before Wi-Fi, fragmenting
the heap that remained after STA initialization. Neither allocation order can
work reliably while the full runtime is source. The runtime is therefore moved
unchanged to host-precompiled `app.mpy`; source `main.py` becomes a tiny
`import app` boot shim.

The plain-HTTP SL and Open-Meteo endpoints were rechecked on 2026-08-06 with
the production query shapes and returned directly without redirects. Their
transport choice is not changed by this work.

## Scope and constraints

### Preserve

- The current runtime architecture and its awake-mode fallback.
- The existing `settings.json` format, including `data_pull_interval_min`,
  `render_interval_min`, and `power.deep_sleep`.
- One request attempt per source per deep-sleep wake.
- One-minute wall-clock cadence and configurable `wake_advance_s`.
- One 48 KB framebuffer, reused for both differential planes.
- Existing full and differential-partial panel command sequences, polarity,
  pin mapping, BUSY polling, and `finally`-guarded panel sleep.
- Current screen design and display data representation.
- USB configuration and the existing setup/awake code paths.

### Exclude

- No merge or cherry-pick of the cleanup branch.
- No deletion of the awake runtime, setup server, Microdot, or asyncio.
- No visual redesign, native Swedish glyph work, or API cadence change.
- No new framework, class hierarchy, or broad module split.
- No panel-driver command changes without a separate hardware diagnosis.
- No merge to `main` until hardware acceptance is complete.

## Targeted production changes

### 1. Deterministic boot diagnostics and watchdog coverage

Every boot prints `machine.reset_cause()` in a stable diagnostic line so
serial logs distinguish power-on, deep-sleep, watchdog, and manual resets.

For deep-sleep mode, configuration is loaded first so a malformed local file
remains Ctrl-C recoverable. A single watchdog is then started and passed into
Wi-Fi connection so the STA driver can reserve its RX buffers on the clean
heap. Exactly one framebuffer is allocated next, before RTC, NTP, API, render,
or EPD work. This WDT -> Wi-Fi -> framebuffer order is hardware-confirmed; the
earlier framebuffer-before-radio design was not viable on the ESP32-WROOM.
The same watchdog covers boundary waiting, API requests, NTP, and panel
orchestration. The awake path keeps its existing watchdog ownership.

The EPD object is constructed only when a changed frame has already been
encoded and the program is ready to refresh. This avoids fragmenting the heap
on no-refresh wakes and protects the one-time framebuffer allocation.

### 2. Fresh Wi-Fi attempt on every deep-sleep wake

Deep sleep remains the reconnect mechanism: every wake rebuilds the network
stack and makes a bounded STA association attempt.

`wifi.connect_sta()` explicitly clears a stale interface state before
activation, starts one connection attempt, polls both `isconnected()` and
available failure status, feeds the supplied watchdog while waiting, and
returns `False` on a terminal status or timeout. It logs a reason without
printing credentials. WLAN is disabled before sleeping.

Missing credentials and unavailable Wi-Fi remain normal outcomes. The wake
renders `Wi-Fi unavailable`, marks retained departures stale, commits that
honest frame, and schedules the next minute rather than raising or entering a
permanent setup mode.

### 3. Backward-compatible configuration validation

`settings.load()` validates the values required by both existing modes while
returning the current dictionary shape. Existing cadence keys and
`power.deep_sleep` remain accepted; unknown fields are not removed merely for
cleanup. Validation covers:

- one or two non-blank stops with positive site IDs;
- direction 1 or 2, forecast 1 through 1200, and one through three departures;
- positive pull/render/full-refresh intervals;
- boolean `power.deep_sleep` and `wake_advance_s` from 0 through 59;
- optional weather coordinates and positive weather intervals.

The Wi-Fi loader continues accepting the existing nested `{"wifi": ...}`
shape. Missing credentials remain recoverable. Malformed configuration gets a
precise serial error and does not start a tight reboot loop.

### 4. Compatible, strictly validated retained state

The compact retained envelope keeps its checksum and 2048-byte limit. Its
schema version is incremented once, forcing one safe full refresh after this
firmware is deployed.

The state adds:

- a renderer revision identifying the pixel contract;
- a compact fingerprint of ordered stop names/IDs, direction, departure count,
  and enabled weather display inputs.

Decoding rejects oversized, corrupt, schema-incompatible,
renderer-incompatible, settings-incompatible, or malformed nested state. It
checks frame length, section count and keys, row shapes, status/weather shape,
and integer-or-`None` timestamps. The existing list-based frame representation
is retained; stable settings compatibility makes its positional stop fallback
safe without changing `display.py`'s public model.

An invalid retained value is treated as absent and therefore forces a full
refresh. It never aborts boot.

### 5. Transactional panel and RTC state

For changed content, the deep-sleep path performs this exact transaction:

1. Build and encode the complete proposed next state, including the size check.
2. Strictly decode those candidate bytes with the current settings fingerprint
   and section count. Abort without touching RTC or EPD if validation fails.
3. Keep the validated previous semantic frame in ordinary RAM.
4. Clear RTC memory and verify that it reads back empty.
5. Construct the EPD object and perform the existing full or differential
   refresh under the panel sleep guard.
6. Write the pre-encoded new retained state.
7. Verify exact RTC bytes and strictly decode them in the current settings
   context.

If encoding fails, neither RTC nor panel is touched. If a reset or exception
occurs after invalidation, the next wake sees no usable old frame and uses a
full refresh. If commit fails after a visible refresh, cleanup clears RTC so
the next wake cannot apply a differential waveform against the wrong image.

For unchanged content, the panel remains untouched and compatible state may be
committed directly. Successful serial display output describes the final new
frame, not the silently reconstructed old differential plane.

### 6. Honest network adapter failures

SL and Open-Meteo continue using one bounded plain-HTTP request in deep-sleep
mode. Each adapter always closes its response and requires:

- a 2xx HTTP status;
- valid JSON;
- the expected top-level collection shape.

SL requires a `departures` list. An empty list is a valid fresh response.
Open-Meteo requires `daily` and `hourly` dictionaries. All other results raise
and flow into the existing stale/error policy.

### 7. Clock-correction safety

Elapsed-time checks share the rule that a retained timestamp in the future is
not fresh. It makes the associated work due immediately. This applies to
weather pulls, daily NTP synchronization, and periodic full refreshes. Weather
usability is checked on every wake; a forecast date mismatch (especially the
first wake after midnight) makes a pull due immediately regardless of elapsed
interval and cannot be displayed as current.

Scheduling still uses the current wall-clock minute boundary after Wi-Fi and
any cold-boot NTP synchronization. Wake delay remains clamped to a future
minute with the configured advance.

A due retained-wake NTP resync runs after the departures request but before
weather date/usability checks and footer construction. This guarantees that a
clock correction across local midnight makes both weather and footer observe
the same corrected local date.

### 8. Bounded deep-sleep recovery

Expected source failures produce screen state and continue normally.
Unexpected exceptions in the deep-sleep path print a traceback, best-effort
turn off WLAN, invalidate RTC state, and call `machine.deepsleep(60_000)`.
Any powered panel is already covered by the existing refresh `finally` guard.
If deep sleep unexpectedly returns, `machine.reset()` is the fallback.
The ordinary successful-cycle deep-sleep call uses the same reset fallback;
returning from either sleep request can never fall through into normal code.

Configuration errors retain the current USB-recoverable behavior rather than
rebooting forever. Awake-mode fatal behavior is not changed by this project.

### 9. Safer deployment activation

`deploy.bat` keeps its current compile-all-source wildcard and does not remove
legacy device files. Full runtime `app.py` is host-compiled and transferred as
`app.mpy` with fonts, bytecode, and optional configuration. Tiny source
`main.py` contains only the documented `import app` boot shim, is the last
firmware file copied, and the board resets only after every copy succeeds.
`main.py` is excluded from compilation and any ignored `main.mpy` is excluded
from support copies.

A failed dependency copy therefore leaves the previously deployed entry point
in place instead of activating a program whose required modules may be only
partially updated.

## Test strategy

Production code changes follow red-green-refactor. Focused host tests are added
before each behavior change.

### Pure-state tests

`tests/test_retained.py` covers:

- version, renderer, settings, checksum, size, and nested-shape rejection;
- ordered-stop and relevant-settings incompatibility;
- JSON tuple/list canonicalization;
- valid round-trip within the RTC limit.

`tests/test_wake_schedule.py` and weather-related tests cover future timestamp
handling and minute-boundary behavior.

### Adapter and Wi-Fi tests

New adapter tests cover non-2xx status, invalid JSON, malformed top-level data,
empty successful departures, one request, and response closure. Wi-Fi tests use
a fake WLAN/time/watchdog boundary to cover stale-interface reset, success,
terminal failure, timeout, and watchdog feeding without importing hardware.

### Deep-sleep orchestration tests

A narrowly scoped fake MicroPython environment imports runtime `app.py` and verifies:

- WDT construction and Wi-Fi STA initialization precede the framebuffer, which
  in turn precedes RTC, NTP, API, render, and EPD work;
- the same framebuffer is reused for old and new planes;
- changed refresh order is encode, strict candidate decode, RTC invalidate,
  panel refresh, RTC commit;
- encode/invalidation/refresh/commit failures cannot leave a falsely usable
  retained frame;
- unchanged content does not touch the panel;
- missing/unavailable Wi-Fi renders an honest state and schedules another wake;
- deep-sleep runtime failure disables STA, invalidates state, requests a
  60-second sleep, and resets if that call returns;
- reset cause is logged;
- full refresh is selected for absent or incompatible state and for a retained
  last-full timestamp in the future; partial refresh is selected only for a
  compatible previous frame. Future weather/NTP timestamps make their own work
  immediately due.

Source-level entrypoint/deployment tests verify that `main.py` is a tiny
`import app` shim, `app.py` participates in host bytecode compilation,
`app.mpy` support copying precedes activation, `main.py` is copied after every
supporting artifact, and `main.mpy` never ships.

### Host verification

Before hardware access:

```text
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
```

Every runtime module, including `app.py`, is compiled with `mpy-cross`; only the
tiny `main.py` import shim remains source as required by MicroPython startup.

## Hardware acceptance on COM3

No merge is proposed until all checks below succeed:

1. Record the device file listing and preserve private configuration without
   printing credentials.
2. Deploy the branch and capture serial output from cold boot.
3. Confirm the first incompatible retained state causes one full refresh.
4. Observe at least five consecutive minute wakes; changed compatible frames
   must use differential partial refresh and every wake must schedule the next.
5. Deliberately invalidate RTC memory, then confirm exactly one full refresh
   followed by partial refresh on the next changed wake.
6. Exercise a user-approved Wi-Fi outage. The screen must show Wi-Fi
   unavailable/stale data, continue minute wakes, and recover automatically
   after Wi-Fi returns.
7. Confirm serial output agrees with the visible screen and no old
   differential frame is reported as newly visible.
8. Confirm the panel sleeps after every refresh and the device filesystem
   contains the intended firmware artifacts.

Hardware-only claims remain unverified until the owner confirms the physical
panel. If acceptance fails, the production fallback is to redeploy `prod-v1`;
the reliability branch remains isolated for further diagnosis.

## Completion criteria

- All identified silent-stale and unsafe-refresh failure paths have regression
  tests and targeted fixes.
- Existing settings and awake behavior remain compatible.
- Pytest, mypy, and all MicroPython compilation checks pass.
- The COM3 wake sequence, retained-state recovery, and Wi-Fi outage tests pass.
- The owner confirms the physical display updates correctly.
- Only then is the branch eligible for review and merge into `main`.
