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


def reconnect(ssid, password, timeout_ms=STA_TIMEOUT_MS):
    """Re-establish a dropped STA link and return True once connected.

    The ESP32 usually auto-reconnects to a known AP on its own, but the most
    likely 24/7 failure is the router power-cycling, and auto-reconnect isn't
    guaranteed to recover from every wedged state -- so display_loop calls
    this as an explicit belt-and-suspenders after several pulls in a row have
    all failed (a strong "connectivity is down", not "SL is down" signal).
    A no-op fast path if the link is actually already up. Toggling the
    interface off/on first clears a wedged association that a bare connect()
    sometimes won't."""
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected():
        return True
    print("wifi: link down -- reconnecting to", ssid)
    try:
        sta.active(False)
    except Exception:
        pass
    return connect_sta(ssid, password, timeout_ms)


def start_ap():
    """Bring up the open setup AP. Returns the WLAN AP interface."""
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, security=0)  # open network -- setup only, temporary
    print("wifi: AP mode,", AP_SSID, "at", ap.ifconfig()[0])
    return ap
