"""Host-isolated contract tests for Wi-Fi STA recovery."""
import importlib
import sys

import pytest


class Clock:
    def __init__(self, ticks):
        self.ticks = list(ticks)
        self.pauses = []

    def ticks_ms(self):
        return self.ticks.pop(0)

    def ticks_diff(self, current, start):
        return current - start

    def sleep_ms(self, milliseconds):
        self.pauses.append(milliseconds)


class Watchdog:
    def __init__(self):
        self.feeds = 0

    def feed(self):
        self.feeds += 1


class Sta:
    def __init__(self, connected, statuses):
        self.connected = list(connected)
        self.statuses = list(statuses)
        self.actions = []

    def active(self, value):
        self.actions.append(("active", value))

    def connect(self, ssid, password):
        self.actions.append(("connect", ssid, password))

    def isconnected(self):
        return self.connected.pop(0)

    def status(self):
        return self.statuses.pop(0)

    def ifconfig(self):
        return ("192.0.2.7",)


class Network:
    STA_IF = 0
    AP_IF = 1

    def __init__(self, sta):
        self.sta = sta

    def WLAN(self, interface):
        assert interface == self.STA_IF
        return self.sta


def load_wifi(monkeypatch, network, clock):
    monkeypatch.setitem(sys.modules, "network", network)
    monkeypatch.delitem(sys.modules, "wifi", raising=False)
    module = importlib.import_module("wifi")
    del sys.modules["wifi"]
    monkeypatch.setattr(module, "time", clock)
    return module


def test_connect_cycles_interface_and_stops_promptly_on_negative_status(monkeypatch):
    """Catches leaving a failed association wedged until the full timeout."""
    sta = Sta([False], [-1])
    clock = Clock([0, 0])
    watchdog = Watchdog()

    connected = load_wifi(monkeypatch, Network(sta), clock).connect_sta(
        "home", "secret", wdt=watchdog)

    assert connected is False
    assert sta.actions == [
        ("active", False),
        ("active", True),
        ("connect", "home", "secret"),
    ]
    assert clock.pauses == [100]
    assert watchdog.feeds == 1


def test_connect_polls_with_watchdog_until_connected(monkeypatch):
    """Catches a connection loop that starves the hardware watchdog."""
    sta = Sta([False, True], [0])
    clock = Clock([0, 0])
    watchdog = Watchdog()

    connected = load_wifi(monkeypatch, Network(sta), clock).connect_sta(
        "home", "secret", wdt=watchdog)

    assert connected is True
    assert clock.pauses == [100, 200]
    assert watchdog.feeds == 1


def test_connect_returns_false_after_the_bounded_timeout(monkeypatch):
    """Catches an association poll that can outlive its connection budget."""
    sta = Sta([False], [0])
    clock = Clock([0, 15001])

    connected = load_wifi(monkeypatch, Network(sta), clock).connect_sta("home", "secret")

    assert connected is False
    assert clock.pauses == [100]


def test_connect_diagnostics_do_not_include_the_password(monkeypatch, capsys):
    """Catches a failed association exposing Wi-Fi credentials in serial logs."""
    password = "do-not-log-this-password"
    sta = Sta([False], [-1])
    clock = Clock([0])

    connected = load_wifi(monkeypatch, Network(sta), clock).connect_sta("home", password)

    assert connected is False
    assert password not in capsys.readouterr().out


def test_reconnect_fast_path_does_not_cycle_an_healthy_interface(monkeypatch):
    """Catches disrupting a link that has already recovered itself."""
    sta = Sta([True], [])
    clock = Clock([])

    connected = load_wifi(monkeypatch, Network(sta), clock).reconnect("home", "secret")

    assert connected is True
    assert sta.actions == []


def test_reconnect_delegates_to_one_fresh_interface_cycle(monkeypatch):
    """Catches reconnect toggling the station twice before connecting."""
    sta = Sta([False], [])
    clock = Clock([])
    wifi = load_wifi(monkeypatch, Network(sta), clock)
    delegated = []

    def connect_sta(ssid, password, timeout_ms):
        delegated.append((ssid, password, timeout_ms))
        return True

    monkeypatch.setattr(wifi, "connect_sta", connect_sta)

    connected = wifi.reconnect("home", "secret")

    assert connected is True
    assert delegated == [("home", "secret", 15000)]
    assert sta.actions == []


def test_fake_adapter_import_is_not_retained_after_its_patch_ends():
    """Catches a fake network dependency leaking into later test imports."""
    missing = object()
    original = sys.modules.pop("wifi", missing)
    try:
        with pytest.MonkeyPatch.context() as patch:
            load_wifi(patch, Network(Sta([True], [])), Clock([]))
        assert sys.modules.get("wifi", missing) is original
    finally:
        sys.modules.pop("wifi", None)
        if original is not missing:
            sys.modules["wifi"] = original
