"""Wi-Fi STA connection for the one wake-cycle attempt."""
import network
import time

STA_TIMEOUT_MS = 15000


def connect_sta(
    ssid: str, password: str, timeout_ms: int = STA_TIMEOUT_MS,
) -> bool:
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
