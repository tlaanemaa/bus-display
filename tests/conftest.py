"""Host pytest needs a stand-in for MicroPython's framebuf module:
display.py imports it for the scaled built-in font's scratch buffer (see
display._scaled_text() -- CLAUDE.md "Testability rule"). Installed into
sys.modules before any test imports display, so the real import
statement in display.py resolves to this fake without display.py having
to know it's running on host.

Not pixel-accurate to the real built-in font (that's compiled into
MicroPython's C source, unavailable on host) -- .text() just solid-fills
each glyph's 8x8 cell. That's enough to exercise layout math and bounds
checking; actual glyph legibility is verified on-device only (CLAUDE.md:
"on-device behavior is verified by eye")."""
import sys
import types

MONO_HLSB = 0


class _FakeFrameBuffer:
    def __init__(self, buf, width, height, fmt):
        self.width = width
        self.height = height
        self.px = [[0] * width for _ in range(height)]

    def fill(self, color):
        for row in self.px:
            for i in range(len(row)):
                row[i] = color

    def fill_rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            if 0 <= yy < self.height:
                row = self.px[yy]
                for xx in range(x, x + w):
                    if 0 <= xx < self.width:
                        row[xx] = color

    def pixel(self, x, y, color=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return 0
        if color is None:
            return self.px[y][x]
        self.px[y][x] = color

    def text(self, s, x, y, color=1):
        # Solid-fill each glyph's 8x8 cell -- see module docstring.
        for i in range(len(s)):
            self.fill_rect(x + i * 8, y, 8, 8, color)


_module = types.ModuleType("framebuf")
_module.FrameBuffer = _FakeFrameBuffer
_module.MONO_HLSB = MONO_HLSB
sys.modules.setdefault("framebuf", _module)
