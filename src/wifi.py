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
    if sta.isconnected():
        # Already up (e.g. after a soft reset, which keeps the radio associated):
        # calling connect() again raises "Wifi Internal State Error".
        print("wifi: already connected to", ssid)
        return True
    try:
        sta.connect(ssid, password)
    except OSError as e:
        # "Wifi Internal State Error": the driver is mid-connect/half-associated
        # (seen after a soft reset). Drop the association and try once more
        # rather than throwing out of boot or the reconnect path.
        print("wifi: connect() raised %s -- disconnecting and retrying" % e)
        try:
            sta.disconnect()
        except Exception:
            pass
        time.sleep_ms(500)
        try:
            sta.connect(ssid, password)
        except OSError as e2:
            print("wifi: connect() failed again:", e2)
            return False

    start = time.ticks_ms()
    while not sta.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
            print("wifi: STA connect to", ssid, "timed out")
            return False
        time.sleep_ms(200)

    print("wifi: connected to", ssid, "ip =", sta.ifconfig()[0])
    return True


BOOT_ATTEMPTS = 3   # after a power cut the router boots slower than the ESP32


def boot_connect(wifi_cfg, attempts=BOOT_ATTEMPTS):
    """Boot-time STA connect with retries. Returns (connected, failed_attempts).
    Called from main.py BEFORE the app is imported: the Wi-Fi driver must claim
    its ESP-IDF memory before the MicroPython heap grows into that pool for the
    app's modules, or DNS/sockets later fail for lack of it (seen on hardware
    2026-11-08: getaddrinfo -202 on every fetch)."""
    if not (wifi_cfg and wifi_cfg.get("ssid")):
        return False, 0
    for attempt in range(attempts):
        if connect_sta(wifi_cfg["ssid"], wifi_cfg.get("password", "")):
            return True, attempt
    return False, attempts


def reconnect(ssid, password, timeout_ms=STA_TIMEOUT_MS, force=False):
    """Re-establish a dropped STA link and return True once connected.

    The ESP32 usually auto-reconnects to a known AP on its own, but the most
    likely 24/7 failure is the router power-cycling, and auto-reconnect isn't
    guaranteed to recover from every wedged state -- so display_loop calls
    this as an explicit belt-and-suspenders after several pulls in a row have
    all failed (a strong "connectivity is down", not "SL is down" signal).
    A no-op fast path if the link is actually already up. Toggling the
    interface off/on first clears a wedged association that a bare connect()
    sometimes won't.

    force=True skips the "already connected" fast path. display_loop passes
    it because "every fetch failing while isconnected() says True" is the
    WEDGED case (router rebooted under us, stale DHCP lease, stuck lwIP) --
    exactly the one a no-op fast path used to leave broken until a replug."""
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected() and not force:
        return True
    print("wifi: reconnecting to", ssid, "(forced)" if force else "(link down)")
    try:
        sta.disconnect()
    except Exception:
        pass
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
