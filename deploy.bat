@echo off
setlocal

rem ===========================================================================
rem Deploy the bus-display project to the ESP32 over USB, then reset it.
rem
rem   Usage:  deploy.bat [COM_PORT]
rem   e.g.    deploy.bat            (auto-detects the connected device)
rem           deploy.bat COM5       (force a specific port)
rem
rem Copies everything under src\ (which maps 1:1 to the device filesystem root)
rem plus src\lib\. The *.py / *.json globs only match files directly in those
rem folders, so host CPython __pycache__\*.pyc junk is skipped automatically --
rem no incremental/diff sync is needed (mpremote has none anyway); a full copy
rem of ~13 small files takes a couple of seconds and is harmless to flash.
rem
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

echo Deploying to %PORTDESC% ...

rem --- top-level files: config, drivers, app modules, settings -------------
rem settings.example.json is a repo-only template -- the device only needs the
rem real settings.json, so skip the example to keep the device clean.
for %%F in ("%SRCDIR%\*.py" "%SRCDIR%\*.json") do (
    if /I not "%%~nxF"=="settings.example.json" (
        echo   cp %%~nxF
        %MP% %CONN% fs cp "%%F" ":%%~nxF"
        if errorlevel 1 goto :fail
    )
)

rem --- vendored libraries (src\lib\ -> :lib) --------------------------------
rem Both microdot.py (source) and microdot.mpy (bytecode) get copied; MicroPython
rem prefers the .mpy and ignores the .py, so the .py is just inert extra flash.
%MP% %CONN% fs mkdir :lib >nul 2>nul
for %%F in ("%SRCDIR%\lib\*.py" "%SRCDIR%\lib\*.mpy") do (
    echo   cp lib/%%~nxF
    %MP% %CONN% fs cp "%%F" ":lib/%%~nxF"
    if errorlevel 1 goto :fail
)

echo Resetting %PORTDESC% ...
%MP% %CONN% reset
if errorlevel 1 goto :fail

echo.
echo Done.
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo ERROR: a command failed. Check that:
echo   - the COM port is correct  (try: %MP% connect list)
echo   - no REPL or serial monitor is holding the port
echo.
pause
endlocal
exit /b 1
