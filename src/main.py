"""Tiny MicroPython boot shim.

The runtime ships as host-precompiled app.mpy so on-device parsing cannot
fragment the heap before Wi-Fi and the resident 48 KB framebuffer need it.
"""
import app
