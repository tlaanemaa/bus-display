# Deep-Sleep-Only Cleanup Design

## Objective

Turn the current dual-mode firmware into one clear, production-oriented
implementation built around a single deep-sleep wake cycle:

`boot -> validate -> connect -> wait for request boundary -> fetch once -> decide -> render -> retain -> deep sleep`

The cleanup must preserve the hardware-confirmed display behavior, one-minute
freshness, and differential partial refresh. It must improve the safety of the
panel's old-frame reference before removing the continuously-awake and setup
portal implementations.

## Chosen approach

Use a safety-first staged cleanup:

1. Characterize important behavior with host tests.
2. Harden retained state and external input boundaries.
3. Reduce the runtime to the proven deep-sleep path.
4. Remove obsolete dependencies, tools, deployment behavior, and historical
   documentation.
5. Verify the result on hardware at explicit checkpoints.

This is preferred over deletion-first because the current retained-state
transaction has a failure window that can leave the physical panel and RTC
state out of sync. It is preferred over a rewrite because the deep-sleep path
already works on hardware and the EPD driver is proven.

## Product decisions

- Deep sleep is the only supported runtime.
- The device wakes once per minute and remains aligned to wall-clock minute
  boundaries.
- `wake_advance_s` remains configurable and defaults to 3 seconds.
- Each API gets one request attempt per wake. The following minute is the
  retry.
- Missing credentials or unavailable Wi-Fi are shown on the panel. The device
  continues waking and retrying without requiring USB access.
- The setup AP and web server are removed. Configuration is managed over USB.
- The framebuffer remains a single resident 48 KB allocation per wake.
- Most changed frames use a differential partial refresh. Periodic full
  refreshes remain configurable and clear ghosting.
- The proven EPD command sequence, polarity, pin mapping, busy handling, and
  sleep guard are not refactored.
- Current rendered geometry is preserved during cleanup. Where documentation
  disagrees with the tested code, including the 14 px content margin, the code
  is the behavioral source of truth unless the owner requests a visual change
  separately.

## Target structure

### Boot and hardware orchestration

`main.py` becomes a small synchronous entry point. It loads validated
configuration, connects Wi-Fi, aligns the request start, invokes one wake
cycle, performs the selected panel operation, persists the result, and enters
deep sleep.

It owns hardware adapters such as RTC memory, watchdog, WLAN, framebuffer,
panel, and `machine.deepsleep()`. It does not own departure, weather, or
refresh policy.

### Validated configuration

`config.py` remains a read-only Wi-Fi credential loader. `settings.py`
validates and normalizes settings once, returning one documented runtime
shape. Runtime code does not scatter defaults through `.get()` calls.

The normalized settings include:

- a non-empty ordered stop list with stable stop identity;
- direction, forecast, and departure count;
- full-refresh interval;
- `wake_advance_s`;
- an optional normalized weather configuration.

The obsolete `data_pull_interval_min`, `render_interval_min`, and
`power.deep_sleep` settings are removed. `wake_advance_s` remains under
`power` for compatibility with the current owner configuration; `power`
contains no mode switch.

Malformed configuration produces a precise serial diagnostic. Missing or
unavailable Wi-Fi remains a normal recoverable display state.

### Pure wake-cycle policy

The decision-making currently embedded in `deep_sleep_cycle()` moves behind a
small pure interface. Given validated settings, fetch outcomes, current time,
and validated retained state, it produces:

- the display frame;
- whether content changed;
- full, partial, or no refresh;
- the next retained semantic state.

The representation remains JSON-native and MicroPython-friendly: typed
dictionaries, lists, strings, integers, booleans, and `None`. It does not add
dataclasses or a second framebuffer.

Important structures receive explicit `TypedDict` and `Literal` contracts:
settings, stops, departures, weather readings, display sections, footer
status, display frame, fetch outcomes, and retained state. Small protocols or
stubs describe hardware objects where needed so mypy can check the boundaries
without importing device modules on the host.

### Display

`display.py` continues to own logical layout, mount rotation, font use,
procedural weather icons, and framebuffer rendering. It is not split into many
small modules solely for aesthetic reasons.

Frame construction is JSON-native from the start. Footer status is an explicit
discriminated value rather than an overloaded string/dictionary sentinel.
Rendering and reporting are separated: reconstructing the old frame for the
`0x10` plane must not log it as newly visible. Serial output describes only the
new frame actually presented to the owner.

### Network adapters

`sl.py` and `openmeteo.py` perform one request and always close the response.
They require a 2xx response and the expected top-level JSON shape before
returning success. A legitimate empty departure list remains successful;
HTTP error bodies and malformed payloads are failures.

Internal retry loops, retry delays, and retry parameters are removed.

## Retained-state and panel transaction

The retained state is the semantic source used to reconstruct the physical
panel's old image after deep sleep. It therefore needs stricter guarantees than
a checksum alone.

### Validation and compatibility

Decoding validates:

- envelope magic, checksum, and maximum size;
- retained schema version;
- renderer revision;
- settings fingerprint;
- complete nested frame and status shapes;
- stop identities and section count;
- numeric timestamp fields and weather structure.

Invalid, malformed, renderer-incompatible, or configuration-incompatible
state is treated as absent. An absent previous frame always forces a full
refresh.

The settings fingerprint covers values that can change rendered meaning or
fallback identity, including ordered stop identities and relevant display
configuration. A failed fetch may reuse old data only when the retained
section has the same stable stop identity.

### Safe refresh transaction

For a content-changing refresh:

1. Encode and size-check the proposed next retained state before touching the
   panel.
2. Keep the validated previous frame in ordinary RAM for a possible partial
   refresh.
3. Clear RTC user memory before starting the panel update.
4. Perform the full or differential partial refresh with the existing
   `finally`-guarded panel sleep.
5. After the refresh succeeds, write the new retained bytes.
6. Read the bytes back and decode them before entering deep sleep.

Any reset or exception after step 3 leaves RTC state empty. The next wake
therefore uses a full refresh instead of applying a differential waveform
against the wrong old frame. If encoding or size validation fails in step 1,
the panel is not changed.

If content is unchanged, the panel is not touched and compatible retained
state may be updated without invalidating the known physical frame.

Renderer revisions are incremented whenever code changes can alter the pixels
produced by the same semantic frame. This deliberately causes one full refresh
after such firmware updates.

## Error handling and recovery

- Configuration errors are reported explicitly and remain recoverable over
  USB. They do not enter a tight reset loop.
- Missing Wi-Fi credentials, connection failure, and request failure are
  expected outcomes represented on the screen.
- Unexpected runtime failures print a traceback, invalidate retained state if
  a panel transaction may have started, safely sleep the panel when it exists,
  and use a bounded reset/deep-sleep recovery path.
- The watchdog remains protection for socket stalls and panel busy waits. Its
  timeout documentation reflects single-attempt requests rather than the
  removed three-retry loop.
- Weather is cleared from both the display and retained state when disabled.
- Weather age calculations reject negative ages caused by clock correction;
  a future timestamp is not considered fresh.
- NTP correction cannot silently suppress weather fetching through a retained
  future bucket.

## Removal inventory

After the safety work is covered by tests:

- Delete `display_loop()`, awake tick/sleep helpers, reconnect counters, and
  duplicate awake-mode fetch/weather/NTP/refresh policy from `main.py`.
- Remove all branching on `power.deep_sleep`.
- Convert the remaining wake cycle to synchronous functions and remove
  `asyncio` plus `typings/asyncio.pyi`.
- Delete `server.py`, vendored Microdot, `wifi.start_ap()`,
  `wifi.reconnect()`, AP constants, and `config.save()`.
- Remove Microdot-specific mypy and ignore configuration.
- Remove awake-only render comparison helpers.
- Remove resolved one-off diagnostic/font experiments after transferring any
  durable assertions into tests.
- Keep calibration, panel test-pattern, layout preview, weather preview, and
  font-generation tools.

No development dependency in `requirements-dev.txt` is removed solely by this
cleanup; Microdot is vendored and asyncio is provided by MicroPython.

## Deployment cleanup

Deployment becomes deterministic. It must not compile or copy stale wildcard
artifacts after their source has been deleted.

- Use an explicit deployed-module manifest or clean generated `.mpy` files
  before compiling the known source set.
- Remove the `src/lib` compile and copy path.
- Copy only the intended configuration/settings JSON files.
- Provide a one-time device cleanup for retired `server.mpy`, Microdot, and
  other obsolete files already present on flash.
- Keep precompilation because avoiding on-device compilation protects the
  clean heap required for the one framebuffer allocation.
- Document that omitting a local credentials file does not implicitly erase
  credentials already stored on the device.

## Test strategy

### Retained state and refresh selection

Host tests cover:

- corruption, truncation, unsupported version, renderer mismatch, settings
  mismatch, malformed nested state, and size overflow;
- stop reorder, insertion, and replacement;
- compatible changed content selecting partial refresh;
- absent or incompatible old state selecting full refresh;
- unchanged content selecting no refresh;
- full-refresh interval expiry;
- RTC invalidation before a changed refresh;
- refresh failure and post-refresh save failure both causing the next wake to
  choose a full refresh.

### Runtime policy

Host tests cover:

- successful empty departures as fresh;
- per-stop failure with identity-safe last-good fallback;
- failure without prior data as `No departures` plus `STALE`;
- offline Wi-Fi status;
- weather success, valid last-good fallback, expiry, prior-day rejection,
  future-age rejection, and disabling;
- status priority between Wi-Fi and weather;
- scheduling around the minute boundary and configurable wake advance.

### Adapter and rendering boundaries

Tests cover non-2xx responses, invalid JSON, malformed top-level payloads,
legitimate empty data, and response closure. Display tests assert the actual
error/status branch rather than layout fit alone. BitFont gets direct tests,
including a clear failure when a glyph exceeds its scratch buffer.

The existing bounds, local-time, weather bucketing, departure ordering, wake
scheduling, retained encoding, and 2x3 layout tests remain.

### Verification commands

Each implementation stage runs the focused failing test first, then the full
host suite:

```text
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy src
```

All deployed modules are also compiled with the repository's `mpy-cross`
workflow before hardware deployment.

## Hardware checkpoints

Hardware verification occurs after:

1. retained-state and adapter hardening;
2. removal of awake/async/setup code;
3. deployment cleanup.

The final verification is:

1. deploy and reset;
2. inspect serial output;
3. have the owner inspect the physical screen;
4. observe at least two consecutive minute wakes;
5. confirm a changed compatible frame uses partial refresh;
6. deliberately invalidate retained compatibility and confirm exactly one
   full refresh;
7. confirm the following changed wake returns to partial refresh;
8. confirm unavailable Wi-Fi is visible and schedules another wake;
9. confirm serial output matches only the final visible frame.

## Documentation cleanup

After runtime behavior is settled, update `AGENTS.md`, `README.md`, module
docstrings, test descriptions, and deployment comments to describe the current
product rather than its exploration history.

Durable hardware constraints and explanations for non-obvious decisions remain
in `AGENTS.md`. Resolved TLS, resident-font, setup-portal, awake-loop, and
retry experiments are condensed or removed from current architecture
sections. `CLAUDE.md` remains a minimal pointer to `AGENTS.md`.

## Non-goals

- No visual redesign or native Swedish glyph work.
- No new onboarding experience.
- No change to the one-minute cadence.
- No change to the physical EPD protocol.
- No attempt to persist a 48 KB framebuffer in RTC memory.
- No generalized framework or class hierarchy for a single-device firmware.
- No battery-life claims until current is measured on real hardware.

## Completion criteria

The cleanup is complete when:

- only the synchronous deep-sleep runtime remains;
- the setup server and continuously-awake code are absent from source and
  deployment;
- retained-state compatibility and refresh transactions fail safely to a full
  refresh;
- one-attempt network failures cannot masquerade as fresh empty data;
- settings and retained structures have explicit checked contracts;
- pytest, mypy, and all `mpy-cross` builds pass;
- the hardware verification sequence succeeds, including partial refresh
  across consecutive wakes and one intentional compatibility-invalid full
  refresh;
- documentation and deployment describe only the resulting architecture.
