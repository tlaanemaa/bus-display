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
rem bytecode + main.py + settings.json + fonts to the device (src\ maps 1:1 to
rem the device filesystem root). Compiling on the host, not the device, is
rem load-bearing on this PSRAM-less board -- on-device compilation fragments
rem the heap and starves the TLS fetch (see the compile section below and
rem CLAUDE.md "RAM-vs-HTTPS conflict"). A full copy of the handful of small
rem files takes a couple of seconds and is harmless to flash.
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

rem mpremote is run as a Python module (same as your working test.bat:
rem "python -m mpremote"). If "python" isn't your launcher, change this line
rem -- e.g. "py -m mpremote" or a full path like
rem "C:\Users\you\anaconda3\python.exe -m mpremote".
set "MP=python -m mpremote"

if not exist "%SRCDIR%\settings.json" (
    echo WARNING: %SRCDIR%\settings.json not found.
    echo          The device needs it to boot into the departures display.
    echo          Copy settings.example.json to settings.json and fill it in.
    echo.
)

rem --- precompile EVERY module to .mpy on the host BEFORE copying ----------
rem The whole app ships as bytecode, not source. Compiling a .py ON THE DEVICE
rem fragments the heap enough to starve the SL/weather TLS handshake -- the
rem largest contiguous free block collapses (confirmed on hardware adding
rem weather: the first fetch hung every boot until these were precompiled; the
rem contiguous free block jumped ~32KB -> ~90KB -- see CLAUDE.md "RAM-vs-HTTPS
rem conflict"). Doing it here means an edit to any .py can NEVER ship as a
rem stale .mpy, and the device never compiles anything but main.py. Needs
rem mpy-cross (pip install mpy-cross).
rem
rem main.py is the ONE exception -- MicroPython auto-runs :main.py by name (no
rem main.mpy is ever run), so it ships as source and is compiled on-device.
rem It's small; with everything else precompiled there's ample contiguous RAM.
for %%F in ("%SRCDIR%\*.py" "%SRCDIR%\lib\*.py") do (
    if /I not "%%~nxF"=="main.py" (
        echo   compile %%~nF.mpy
        python -m mpy_cross "%%F"
        if errorlevel 1 (
            echo ERROR: mpy-cross failed for %%~nxF -- is mpy-cross installed?  pip install mpy-cross
            goto :fail
        )
    )
)

echo Deploying to %PORTDESC% ...

rem --- top-level files: main.py (source), the compiled modules, settings ---
rem Only the .mpy go to the device -- NOT the .py (except main.py). Shipping
rem the source too would be inert dead flash (MicroPython always prefers the
rem .mpy) and just invites confusion about which one runs. settings.example.json
rem is a repo-only template -- the device needs the real settings.json only.
for %%F in ("%SRCDIR%\main.py" "%SRCDIR%\*.mpy" "%SRCDIR%\*.json") do (
    if /I not "%%~nxF"=="settings.example.json" (
        echo   cp %%~nxF
        %MP% %CONN% fs cp "%%F" ":%%~nxF"
        if errorlevel 1 goto :fail
    )
)

rem --- vendored libraries (src\lib\ -> :lib) --------------------------------
rem Compiled above alongside our own modules; only the .mpy ships.
%MP% %CONN% fs mkdir :lib >nul 2>nul
for %%F in ("%SRCDIR%\lib\*.mpy") do (
    echo   cp lib/%%~nxF
    %MP% %CONN% fs cp "%%F" ":lib/%%~nxF"
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
