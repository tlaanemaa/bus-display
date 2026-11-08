@echo off
setlocal
rem ===========================================================================
rem Copy the device's field log (flash, survives power loss) to logs\ on this PC.
rem
rem   Usage:  tools\get_logs.bat [COM_PORT]     (no arg = auto-detect)
rem
rem Plug the board in over USB first. Connecting mpremote interrupts main.py
rem (Ctrl-C), so the script RESETS the board at the end -- it comes back from RTC
rem memory with the same departures and a normal partial refresh (see CLAUDE.md
rem "Resilience"). Close any open REPL first -- only one program can hold the port.
rem
rem Output: logs\<date>_<time>_log.txt  (older half, if any, first in the file)
rem See CLAUDE.md "Field log" for what the lines mean.
rem ===========================================================================
if "%~1"=="" (set "CONN=connect auto") else (set "CONN=connect %~1")
set "MP=python -m mpremote"
set "OUT=%~dp0..\logs"
if not exist "%OUT%" mkdir "%OUT%"
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "TS=%%T"
set "DEST=%OUT%\%TS%_log.txt"

%MP% %CONN% fs cp :log.old.txt "%OUT%\_old.tmp" >nul 2>nul
%MP% %CONN% fs cp :log.txt "%OUT%\_new.tmp"
if errorlevel 1 (
    echo ERROR: could not read :log.txt -- is the board plugged in and the port free?
    exit /b 1
)
if exist "%OUT%\_old.tmp" (
    copy /b "%OUT%\_old.tmp" + "%OUT%\_new.tmp" "%DEST%" >nul
    del "%OUT%\_old.tmp"
) else (
    copy /b "%OUT%\_new.tmp" "%DEST%" >nul
)
del "%OUT%\_new.tmp"
echo Saved %DEST%
echo Restarting the display ...
%MP% %CONN% reset
endlocal
