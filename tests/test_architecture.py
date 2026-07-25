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


def test_deploy_requires_connected_retired_file_cleanup():
    batch = Path("deploy.bat").read_text(encoding="utf-8")
    probe = "rem --- prove the device connection before required cleanup"
    cleanup = "rem --- required retired-file cleanup"
    copy_main = "echo   cp main.py"

    assert batch.index(probe) < batch.index(cleanup) < batch.index(copy_main)
    assert "%MP% %CONN% exec \"import os; os.ilistdir()\"" in batch
    assert "if errorlevel 1 goto :fail" in batch[batch.index(probe):batch.index(cleanup)]

    cleanup_block = batch[batch.index(cleanup):batch.index(copy_main)]
    for removal in (
        "os.remove('server.mpy') if 'server.mpy' in root else None",
        "os.remove('main.mpy') if 'main.mpy' in root else None",
        "os.remove('lib/microdot.mpy') if 'lib' in root",
        "os.rmdir('lib') if 'lib' in root else None",
    ):
        assert removal in cleanup_block
    assert "os.ilistdir" in cleanup_block
    assert "fs rm" not in cleanup_block
    assert ">nul" not in cleanup_block
    assert "if errorlevel 1 goto :fail" in cleanup_block
