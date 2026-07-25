@echo off
setlocal

rem ===========================================================================
rem Deploy the bus-display project to the ESP32 over USB, then reset it.
rem
rem   Usage:  deploy.bat [COM_PORT]
rem   e.g.    deploy.bat            (auto-detects the connected device)
rem           deploy.bat COM5       (force a specific port)
rem
rem COMPILES every module to .mpy on the host (mpy-cross), then copies the
rem bytecode + main.py + settings.json + config.json + fonts to the device
rem (src\ maps 1:1 to the device filesystem root). Compiling on the host, not
rem the device, is load-bearing on this PSRAM-less board: on-device compilation
rem fragments the heap and can prevent the early framebuffer reservation. A
rem full copy of the explicit small file set is deterministic and harmless to
rem flash.
rem
rem Requires mpy-cross:  pip install mpy-cross
rem Close any open REPL / serial monitor first -- only one process can hold the
rem COM port at a time.
rem ===========================================================================

rem With no arg, use mpremote's auto-detect (same as running "python -m mpremote"
rem with no port -- it picks the one connected device, whatever COM it's on).
rem Pass a port to force it, e.g. deploy.bat COM5.
if "%~1"=="" (
    set "CONN=connect auto"
    set "PORTDESC=auto-detected device"
) else (
    set "CONN=connect %~1"
    set "PORTDESC=%~1"
)

set "SRCDIR=%~dp0src"

rem Use the repo-local environment so deploy is reproducible and does not
rem depend on a global Python being on PATH. Set it up per README.md first.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: project virtual environment not found.
    echo        Create it with the commands in README.md.
    goto :fail
)
set "MP=%PY% -m mpremote"
set "MODULES=bitfont config cycle departures display epd7in5v2 localtime models openmeteo refresh_txn retained settings sl wake_schedule weather wifi"

if not exist "%SRCDIR%\settings.json" (
    echo WARNING: %SRCDIR%\settings.json not found.
    echo          The device needs it to boot into the departures display.
    echo          Copy settings.example.json to settings.json and fill it in.
    echo.
)

if not exist "%SRCDIR%\config.json" (
    echo WARNING: %SRCDIR%\config.json not found.
    echo          Existing device Wi-Fi credentials are preserved.
    echo.
)

rem --- precompile EVERY module to .mpy on the host BEFORE copying ----------
rem The whole app ships as bytecode, not source. Compiling a .py ON THE DEVICE
rem fragments the heap and can prevent the one 48 KB framebuffer reservation.
rem Doing it here means an edit to any .py can NEVER ship as a stale .mpy, and
rem the device never compiles anything but main.py. Needs mpy-cross
rem (pip install mpy-cross).
rem
rem main.py is the ONE exception -- MicroPython auto-runs :main.py by name (no
rem main.mpy is ever run), so it ships as source and is compiled on-device.
rem The explicit list prevents deleted source from being copied back as a stale
rem bytecode artifact.
for %%M in (%MODULES%) do (
    echo   compile %%M.mpy
    "%PY%" -m mpy_cross "%SRCDIR%\%%M.py"
    if errorlevel 1 goto :fail
)

echo Deploying to %PORTDESC% ...

rem --- prove the device connection before required cleanup ------------------
%MP% %CONN% exec "import os; os.ilistdir()"
if errorlevel 1 goto :fail

rem --- required retired-file cleanup ----------------------------------------
rem An absent target is successful. Any connection, execution, or non-empty
rem directory error must fail deployment instead of leaving stale firmware.
%MP% %CONN% exec "import os; root=[entry[0] for entry in os.ilistdir('.')]; (os.remove('server.mpy') if 'server.mpy' in root else None); (os.remove('main.mpy') if 'main.mpy' in root else None); (os.remove('lib/microdot.mpy') if 'lib' in root and 'microdot.mpy' in [entry[0] for entry in os.ilistdir('lib')] else None); (os.rmdir('lib') if 'lib' in root else None)"
if errorlevel 1 goto :fail

rem --- top-level files: main.py (source), explicit bytecode, local config ---
echo   cp main.py
%MP% %CONN% fs cp "%SRCDIR%\main.py" :main.py
if errorlevel 1 goto :fail

for %%M in (%MODULES%) do (
    echo   cp %%M.mpy
    %MP% %CONN% fs cp "%SRCDIR%\%%M.mpy" :%%M.mpy
    if errorlevel 1 goto :fail
)

if exist "%SRCDIR%\settings.json" (
    echo   cp settings.json
    %MP% %CONN% fs cp "%SRCDIR%\settings.json" :settings.json
    if errorlevel 1 goto :fail
)

if exist "%SRCDIR%\config.json" (
    echo   cp config.json
    %MP% %CONN% fs cp "%SRCDIR%\config.json" :config.json
    if errorlevel 1 goto :fail
)

rem --- streamed bitmap fonts (src\fonts\ -> :fonts) -------------------------
rem The .fnt files bitfont.py reads glyph-by-glyph from flash (see
rem tools\gen_font.py). Small (~26 KB total) and never held resident.
%MP% %CONN% fs mkdir :fonts >nul 2>nul
for %%F in ("%SRCDIR%\fonts\*.fnt") do (
    echo   cp fonts/%%~nxF
    %MP% %CONN% fs cp "%%F" ":fonts/%%~nxF"
    if errorlevel 1 goto :fail
)

echo Resetting %PORTDESC% ...
%MP% %CONN% reset
if errorlevel 1 goto :fail

echo.
echo Done.
echo.
endlocal
exit /b 0

:fail
echo.
echo ERROR: a command failed. Check that:
echo   - the COM port is correct  (try: %MP% connect list)
echo   - no REPL or serial monitor is holding the port
echo.
endlocal
exit /b 1
