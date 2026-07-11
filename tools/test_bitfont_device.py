"""On-device smoke test for streamed fonts under the real memory
pressure that crashed font_to_py: allocate the 48KB framebuffer FIRST,
then open the fonts and stream-draw text, checking nothing MemoryErrors
and how much heap is left. Run WITHOUT copying (hardware experiment):

    mpremote connect COM3 run tools/test_bitfont_device.py

Fonts + bitfont.py must already be on the device (deploy them first)."""
import gc
import framebuf
import bitfont

gc.collect()
print("free at start          :", gc.mem_free())

WIDTH, HEIGHT = 800, 480
buf = bytearray(WIDTH * HEIGHT // 8)   # 48000 bytes -- the real framebuffer
fb = framebuf.FrameBuffer(buf, WIDTH, HEIGHT, framebuf.MONO_HLSB)
gc.collect()
print("free with 48KB fb alloc:", gc.mem_free())

hero = bitfont.Font("fonts/bitter_hero.fnt")
head = bitfont.Font("fonts/bitter_head.fnt")
row = bitfont.Font("fonts/bitter_row.fnt")
gc.collect()
print("free with 3 fonts open :", gc.mem_free())
print("hero height/baseline   :", hero.height, hero.baseline)
print("measure '14:43'        :", hero.measure("14:43"))
print("measure 'Gustavsberg'  :", head.measure("Gustavsberg centrum"))


def plot(fb, lx, ly, n, c):
    for i in range(n):
        fb.pixel(lx + i, ly, c)


hero.draw("14:43", 0, 0, 1, fb, plot)
head.draw("Gustavsberg centrum", 0, 120, 1, fb, plot)
row.draw("440  Slussen  14 min", 0, 200, 1, fb, plot)
gc.collect()
print("free after draws       :", gc.mem_free())
print("SMOKE TEST OK")
