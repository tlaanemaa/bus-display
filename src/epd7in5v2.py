"""MicroPython driver for the Waveshare 7.5" e-Paper V2 (800x480) panel.

Line-for-line port of Waveshare's epd7in5_V2.py reference driver
(RaspberryPi_JetsonNano/python/lib/waveshare_epd/, fetched 2026-07-04):
same command bytes and order for init/clear/display/sleep, same two-plane
(0x10 + 0x13) write on every refresh, same 0x71-before-each-busy-read
polling. See CLAUDE.md "Hardware" before changing any command byte here.

init_fast/init_part/4-gray/partial-refresh from the reference driver are
not ported -- add them only when the project actually needs them.
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

    def sleep(self):
        self._command(0x50)
        self._data_byte(0xF7)
        self._command(0x02)  # power off
        self._read_busy()
        self._command(0x07)  # deep sleep
        self._data_byte(0xA5)
        time.sleep_ms(2000)
