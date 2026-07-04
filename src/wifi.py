"""Wi-Fi: STA connect with timeout, AP-mode fallback for provisioning.

Verified against this board's firmware (MicroPython v1.28.0 ESP32_GENERIC,
2026-07-04): WLAN.config(essid=..., security=0) sets an open AP, and the
AP interface's default address is 192.168.4.1.
"""
import network
import time

AP_SSID = "BusDisplay-Setup"
STA_TIMEOUT_MS = 15000


def connect_sta(ssid, password, timeout_ms=STA_TIMEOUT_MS):
    """Try to join `ssid`. Returns True on success, False on timeout."""
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect(ssid, password)

    start = time.ticks_ms()
    while not sta.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
            print("wifi: STA connect to", ssid, "timed out")
            return False
        time.sleep_ms(200)

    print("wifi: connected to", ssid, "ip =", sta.ifconfig()[0])
    return True


def start_ap():
    """Bring up the open setup AP. Returns the WLAN AP interface."""
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, security=0)  # open network -- setup only, temporary
    print("wifi: AP mode,", AP_SSID, "at", ap.ifconfig()[0])
    return ap
