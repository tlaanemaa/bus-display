@echo off
rem ===========================================================================
rem Build the bus display MicroPython firmware in Docker (Docker Desktop must be
rem running). Output: firmware\dist\bus-display-firmware.bin
rem See CLAUDE.md "Custom firmware" for what it contains and how to flash it.
rem ===========================================================================
setlocal
set "REPO=%~dp0.."
docker volume create busdisplay-mpy >nul
docker run --rm -v busdisplay-mpy:/mpy -v "%REPO%":/repo espressif/idf:v5.5.1 bash /repo/firmware/build.sh
if errorlevel 1 (
    echo ERROR: firmware build failed.
    exit /b 1
)
endlocal
