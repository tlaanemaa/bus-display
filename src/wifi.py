"""Wi-Fi STA connect/reconnect, plus an unused legacy setup-AP helper.

Configuration is now USB-only. `start_ap()` remains solely as cleanup debt
until the deprecated onboarding flow is removed.
"""
import network
import time

if False:
    from typing import Any

AP_SSID = "BusDisplay-Setup"
STA_TIMEOUT_MS = 15000


def connect_sta(
    ssid: str, password: str, timeout_ms: int = STA_TIMEOUT_MS,
    wdt: "Any | None" = None,
) -> bool:
    """Try to join `ssid`. Returns True on success, False on timeout."""
    sta = network.WLAN(network.STA_IF)
    sta.active(False)
    time.sleep_ms(100)
    sta.active(True)
    sta.connect(ssid, password)

    start = time.ticks_ms()
    while not sta.isconnected():
        if wdt is not None:
            wdt.feed()
        if sta.status() < 0:
            print("wifi: STA connect to", ssid, "failed")
            return False
        if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
            print("wifi: STA connect to", ssid, "timed out")
            return False
        time.sleep_ms(200)

    print("wifi: connected to", ssid, "ip =", sta.ifconfig()[0])
    return True


def reconnect(
    ssid: str, password: str, timeout_ms: int = STA_TIMEOUT_MS,
) -> bool:
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
    return connect_sta(ssid, password, timeout_ms)


def start_ap() -> "object":
    """Legacy unused setup helper. Bring up and return the open AP."""
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, security=0)  # open network -- setup only, temporary
    print("wifi: AP mode,", AP_SSID, "at", ap.ifconfig()[0])
    return ap
