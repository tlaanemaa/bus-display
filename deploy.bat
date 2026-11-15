@echo off
setlocal EnableDelayedExpansion

rem ===========================================================================
rem Deploy the bus display to the ESP32 over USB, then reset it.
rem
rem   Usage:  deploy.bat [files] [COM_PORT]
rem     deploy.bat          build the firmware (app frozen in), flash it, copy
rem                         main.py + settings.json + fonts, remove stale .mpy
rem     deploy.bat files    skip build + flash: only copy main.py + settings.json
rem                         + fonts (e.g. a settings-only change)
rem     deploy.bat COM5     force a port (default: auto-detect)
rem
rem The app's modules are FROZEN into a custom MicroPython firmware (see
rem CLAUDE.md "Custom firmware"): they run from flash instead of RAM, which is
rem what gives the Wi-Fi stack its memory. So a code change means rebuilding
rem and re-flashing the firmware -- firmware\build.bat (Docker Desktop must be
rem running) then esptool. Flashing writes only the firmware region; the
rem board's file system (config.json with the Wi-Fi creds, settings.json, the
rem field log) is kept.
rem
rem Files on the board SHADOW frozen modules (sys.path = ['', '.frozen', ...]),
rem so this script deletes any app .mpy left on the board -- otherwise the old
rem bytecode would run, and cost its RAM again. To try a one-module change
rem without re-flashing, copy just that .mpy by hand (mpy-cross + mpremote
rem fs cp); the next full deploy removes it again.
rem
rem Requires: Docker Desktop, esptool, mpremote (pip install esptool mpremote).
rem Close any open REPL / serial monitor first -- only one program can hold
rem the COM port.
rem ===========================================================================

set "FILES_ONLY="
set "PORT="
for %%A in (%*) do (
    if /I "%%~A"=="files" (set "FILES_ONLY=1") else (set "PORT=%%~A")
)
if defined PORT (
    set "CONN=connect %PORT%"
    set "ESPPORT=--port %PORT%"
) else (
    set "CONN=connect auto"
    set "ESPPORT="
)

set "ROOT=%~dp0"
set "SRCDIR=%ROOT%src"
set "MP=python -m mpremote"
set "FW=%ROOT%firmware\dist\bus-display-firmware.bin"

if not exist "%SRCDIR%\settings.json" (
    echo WARNING: %SRCDIR%\settings.json not found.
    echo          The device needs it to boot into the departures display.
    echo          Copy settings.example.json to settings.json and fill it in.
    echo.
)

if not defined FILES_ONLY (
    echo === Building firmware ...
    call "%ROOT%firmware\build.bat"
    if errorlevel 1 goto :fail
    echo === Flashing firmware ^(file system is kept^) ...
    python -m esptool %ESPPORT% --baud 460800 write-flash 0x1000 "%FW%"
    if errorlevel 1 goto :fail
)

rem Right after a flash (or any reset) the board is still booting and mpremote
rem can't get a REPL ("could not enter raw repl"): wait until it answers.
set /a TRIES=0
:waitrepl
%MP% %CONN% exec "pass" >nul 2>nul
if not errorlevel 1 goto :replok
set /a TRIES+=1
if %TRIES% geq 8 goto :fail
timeout /t 3 /nobreak >nul
goto :waitrepl
:replok

echo === Copying files ...
for %%F in ("%SRCDIR%\main.py" "%SRCDIR%\settings.json") do (
    if exist "%%F" (
        echo   cp %%~nxF
        %MP% %CONN% fs cp "%%F" ":%%~nxF"
        if errorlevel 1 goto :fail
    )
)
%MP% %CONN% fs mkdir :fonts >nul 2>nul
for %%F in ("%SRCDIR%\fonts\*.fnt") do (
    echo   cp fonts/%%~nxF
    %MP% %CONN% fs cp "%%F" ":fonts/%%~nxF"
    if errorlevel 1 goto :fail
)

rem --- remove .mpy that would shadow the frozen modules -------------------
set "NAMES="
for %%F in ("%SRCDIR%\*.py") do (
    if /I not "%%~nxF"=="main.py" set "NAMES=!NAMES!'%%~nF.mpy',"
)
for %%F in ("%SRCDIR%\lib\*.py") do set "NAMES=!NAMES!'lib/%%~nF.mpy',"
echo === Removing stale .mpy that would shadow frozen modules ...
%MP% %CONN% exec "import os; L=os.listdir(); LL=os.listdir('lib') if 'lib' in L else []; [print('  removed', n) or os.remove(n) for n in (%NAMES%) if n in L or (n[:4]=='lib/' and n[4:] in LL)]"
if errorlevel 1 goto :fail

echo === Resetting ...
%MP% %CONN% reset
if errorlevel 1 goto :fail

echo.
echo Done.
echo.
endlocal
exit /b 0

:fail
echo.
echo ERROR: a step failed. Check that:
echo   - Docker Desktop is running (for the firmware build)
echo   - the COM port is correct  (try: %MP% connect list)
echo   - no REPL or serial monitor is holding the port
echo.
endlocal
exit /b 1
