from pathlib import Path


def test_removed_runtime_is_absent():
    source = Path("src/main.py").read_text(encoding="utf-8")
    for obsolete in (
        "display_loop", "asyncio", "_seconds_to_next_tick",
        "_sleep_until_next_tick", "_WIFI_RECONNECT_AFTER_FAILS",
        'get("deep_sleep"',
    ):
        assert obsolete not in source


def test_setup_portal_sources_are_absent():
    assert not Path("src/server.py").exists()
    assert not Path("src/lib/microdot.py").exists()
