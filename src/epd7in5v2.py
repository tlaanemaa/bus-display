"""MicroPython driver for the Waveshare 7.5" e-Paper V2 (800x480) panel.

Line-for-line port of Waveshare's epd7in5_V2.py reference driver
(RaspberryPi_JetsonNano/python/lib/waveshare_epd/, fetched 2026-07-04):
same command bytes and order for init/clear/display/sleep, same two-plane
(0x10 + 0x13) write on every refresh, same 0x71-before-each-busy-read
polling. See CLAUDE.md "Hardware" before changing any command byte here.

init_fast/4-gray from the reference driver are not ported -- add them
only when the project actually needs them. init_part/display_Partial ARE
ported (2026-07-09, see CLAUDE.md "Screen refresh strategy") for the
hybrid full/partial refresh policy in main.py.
"""
from machine import Pin, SPI
import time

WIDTH = 800
HEIGHT = 480
BUF_SIZE = WIDTH * HEIGHT // 8  # 48000
_CHUNK = 512  # never hold two BUF_SIZE-sized buffers at once -- see CLAUDE.md RAM notes


class EPD7in5V2:
    def __init__(self):
        self.width = WIDTH
        self.height = HEIGHT
        self._rst = Pin(26, Pin.OUT)
        self._dc = Pin(27, Pin.OUT)
        self._cs = Pin(15, Pin.OUT, value=1)
        self._busy = Pin(25, Pin.IN)
        self._spi = SPI(2, baudrate=4_000_000, polarity=0, phase=0,
                         sck=Pin(13), mosi=Pin(14))

    def _reset(self):
        self._rst(1)
        time.sleep_ms(20)
        self._rst(0)
        time.sleep_ms(2)
        self._rst(1)
        time.sleep_ms(20)

    def _command(self, cmd):
        self._dc(0)
        self._cs(0)
        self._spi.write(bytes([cmd]))
        self._cs(1)

    def _data_byte(self, val):
        self._dc(1)
        self._cs(0)
        self._spi.write(bytes([val]))
        self._cs(1)

    def _write_bulk(self, buf):
        """Stream buf as one continuous SPI transfer (single dc/cs session),
        in fixed-size chunks so the caller's buffer is never copied."""
        self._dc(1)
        self._cs(0)
        mv = memoryview(buf)
        for offset in range(0, len(buf), _CHUNK):
            self._spi.write(mv[offset:offset + _CHUNK])
        self._cs(1)

    def _write_bulk_inverted(self, buf):
        """Stream bitwise-NOT of buf as one continuous SPI transfer, using a
        small reusable scratch buffer instead of allocating a second
        BUF_SIZE buffer. Two 48 KB buffers alive at once can MemoryError
        even with plenty of nominal free heap, because MicroPython's
        allocator doesn't defragment -- see CLAUDE.md."""
        self._dc(1)
        self._cs(0)
        scratch = bytearray(_CHUNK)
        mv = memoryview(scratch)
        n = len(buf)
        for offset in range(0, n, _CHUNK):
            size = min(_CHUNK, n - offset)
            for i in range(size):
                scratch[i] = (~buf[offset + i]) & 0xFF
            self._spi.write(mv[:size])
        self._cs(1)

    def _write_fill(self, value, count):
        """Stream `count` repetitions of `value` without allocating a
        count-sized buffer."""
        self._dc(1)
        self._cs(0)
        scratch = bytearray(_CHUNK)
        for i in range(_CHUNK):
            scratch[i] = value
        mv = memoryview(scratch)
        written = 0
        while written < count:
            n = min(_CHUNK, count - written)
            self._spi.write(mv[:n])
            written += n
        self._cs(1)

    def _read_busy(self):
        self._command(0x71)
        while self._busy.value() == 0:  # active-low: 0 = busy
            self._command(0x71)
        time.sleep_ms(20)

    def init(self):
        self._reset()

        self._command(0x06)  # booster soft start
        for b in (0x17, 0x17, 0x28, 0x17):
            self._data_byte(b)

        self._command(0x01)  # power setting
        for b in (0x07, 0x07, 0x28, 0x17):
            self._data_byte(b)

        self._command(0x04)  # power on
        time.sleep_ms(100)
        self._read_busy()

        self._command(0x00)  # panel setting
        self._data_byte(0x1F)

        self._command(0x61)  # resolution: 800 x 480
        for b in (0x03, 0x20, 0x01, 0xE0):
            self._data_byte(b)

        self._command(0x15)
        self._data_byte(0x00)

        self._command(0x50)  # VCOM and data interval
        for b in (0x10, 0x07):
            self._data_byte(b)

        self._command(0x60)  # TCON setting
        self._data_byte(0x22)

    def init_part(self):
        """Enter partial-refresh mode. Same hardware reset + panel-setting
        + power-on sequence as init(), but skips resolution/VCOM/TCON
        setup and instead sets the partial-mode booster registers
        (0xE0/0xE5) -- ported verbatim from Waveshare's epd7in5_V2.py
        init_part(), fetched raw 2026-07-09. Do not add the resolution/
        VCOM/TCON calls from init() here "for safety" -- the reference
        doesn't have them in this function, don't re-derive.

        Called before EVERY partial refresh in the current differential
        design (2026-07-10). The hardware reset here wipes the controller's
        old-image RAM, but that no longer matters: partial_old() re-uploads
        the correct previous image on 0x10 explicitly, so the differential
        has a valid reference regardless of what reset left behind. This
        replaces the earlier "init_part() once per run, keep panel awake"
        approach -- see partial_old() and main.py's _draw_and_refresh()."""
        self._reset()
        self._command(0x00)  # panel setting
        self._data_byte(0x1F)
        self._command(0x04)  # power on
        time.sleep_ms(100)
        self._read_busy()
        self._command(0xE0)
        self._data_byte(0x02)
        self._command(0xE5)
        self._data_byte(0x6E)

    def clear(self):
        self._command(0x10)
        self._write_fill(0xFF, BUF_SIZE)
        self._command(0x13)
        self._write_fill(0x00, BUF_SIZE)
        self._command(0x12)
        time.sleep_ms(100)
        self._read_busy()

    def display(self, buf):
        """buf: bytearray/bytes of length BUF_SIZE in framebuf.MONO_HLSB
        layout. Bit 0 = white, bit 1 = black (the panel's native wire
        format -- see CLAUDE.md "Pixel polarity"). A framebuf filled with
        fill(0) and drawn with color 1 for black needs no conversion.
        """
        if len(buf) != BUF_SIZE:
            raise ValueError("buffer must be %d bytes, got %d" % (BUF_SIZE, len(buf)))
        self._command(0x10)
        self._write_bulk_inverted(buf)
        self._command(0x13)
        self._write_bulk(buf)
        self._command(0x12)
        time.sleep_ms(100)
        self._read_busy()

    # --- Differential partial refresh (2026-07-10) ------------------------
    #
    # A partial refresh on the 7.5" V2 is a *differential* update: the
    # controller drives each pixel from whatever is in its 0x10 "old image"
    # RAM to the 0x13 "new image" RAM, using a gentle no-flash waveform.
    # Waveshare's stock display_Partial() (and this driver's earlier
    # display_partial()) sends ONLY the 0x13 plane, leaving 0x10 as
    # stale/garbage -- so pixels get driven against a wrong reference and
    # ghosting/corruption accumulates. The fix (per the betterepd7in5
    # community driver) is to send the ACTUAL previously-displayed image on
    # 0x10 as well, so only genuinely-changed pixels move. Confirmed against
    # betterepd7in5's source: it sends 0x10 (old) + 0x13 (new) every partial.
    #
    # These are split into begin/old/new so the caller can serve BOTH planes
    # from a SINGLE 48 KB buffer -- render the old frame, stream it to 0x10,
    # re-render the new frame into the same buffer, stream it to 0x13. Once
    # bytes are shifted out over SPI they live in the panel controller's RAM,
    # not ESP32 RAM, so overwriting the Python buffer between planes is safe.
    # This keeps the "never hold two BUF_SIZE buffers alive at once" rule
    # (see CLAUDE.md RAM notes) -- do NOT add a display_partial_diff(old, new)
    # that takes two buffers.
    #
    # Enter partial mode with init_part() (once, right before this sequence),
    # then: partial_begin() -> partial_old(old_buf) -> partial_new(new_buf).
    # main.py's _draw_and_refresh() drives this and sleeps the panel after.

    def partial_begin(self):
        """Set the partial-mode VCOM/data interval and select the full-frame
        (0,0)..(WIDTH,HEIGHT) partial window. Full-frame (not a cropped
        sub-rectangle) matches Waveshare's own demo usage and avoids
        byte-alignment window math; the calibrated drawable area already
        covers most of the panel, so a cropped window would save little."""
        self._command(0x50)
        self._data_byte(0xA9)
        self._data_byte(0x07)

        self._command(0x91)  # enter partial mode
        self._command(0x90)  # partial window: full frame, (0,0) .. (WIDTH,HEIGHT)
        for b in (0x00, 0x00, (WIDTH - 1) // 256, (WIDTH - 1) % 256,
                  0x00, 0x00, (HEIGHT - 1) // 256, (HEIGHT - 1) % 256, 0x01):
            self._data_byte(b)

    def partial_old(self, buf):
        """Send the previously-displayed image as the 0x10 "old" plane, so
        the differential update below only drives pixels that actually
        changed. buf is the same MONO_HLSB layout as display()/partial_new.

        POLARITY (one empirical unknown, confirm on hardware): this inverts
        buf to match partial_new's 0x13 plane, which is confirmed legible
        inverted on this panel. If changed pixels ghost/darken instead of
        resolving cleanly, switch this to self._write_bulk(buf) (non-inverted)
        -- that's the whole fix if the starting polarity is wrong."""
        if len(buf) != BUF_SIZE:
            raise ValueError("buffer must be %d bytes, got %d" % (BUF_SIZE, len(buf)))
        self._command(0x10)
        self._write_bulk_inverted(buf)

    def partial_new(self, buf):
        """Send the new image as the 0x13 plane and trigger the partial
        refresh. Call after partial_begin() and partial_old(). Inverted to
        match the earlier working display_partial() polarity."""
        if len(buf) != BUF_SIZE:
            raise ValueError("buffer must be %d bytes, got %d" % (BUF_SIZE, len(buf)))
        self._command(0x13)
        self._write_bulk_inverted(buf)

        self._command(0x12)
        time.sleep_ms(100)
        self._read_busy()

    def sleep(self):
        self._command(0x50)
        self._data_byte(0xF7)
        self._command(0x02)  # power off
        self._read_busy()
        self._command(0x07)  # deep sleep
        self._data_byte(0xA5)
        time.sleep_ms(2000)
