#!/bin/bash
# Build the bus display firmware. Runs INSIDE the espressif/idf:v5.5.1 container
# (use firmware/build.bat from Windows). See CLAUDE.md "Custom firmware".
#
#   /repo = this repository (bind mount)
#   /mpy  = a Docker volume holding the MicroPython checkout and build output
#           (kept off the Windows bind mount: the build is far faster there)
set -euo pipefail

MPY_TAG=v1.28.0
MPY=/mpy/micropython
BOARD_DIR=/repo/firmware/boards/BUS_DISPLAY
BUILD=/mpy/build-BUS_DISPLAY
OUT=/repo/firmware/dist

if [ ! -d "$MPY/.git" ]; then
    git clone --depth 1 --branch "$MPY_TAG" https://github.com/micropython/micropython.git "$MPY"
fi
# The volume may hold an older checkout: refuse rather than silently mix versions.
have=$(git -C "$MPY" describe --tags --exact-match 2>/dev/null || echo unknown)
if [ "$have" != "$MPY_TAG" ]; then
    echo "ERROR: $MPY is at $have, expected $MPY_TAG (docker volume rm busdisplay-mpy to start over)" >&2
    exit 1
fi

make -C "$MPY/mpy-cross" -j"$(nproc)"
cd "$MPY/ports/esp32"
make BOARD_DIR="$BOARD_DIR" BUILD="$BUILD" submodules
make BOARD_DIR="$BOARD_DIR" BUILD="$BUILD" -j"$(nproc)"

mkdir -p "$OUT"
cp "$BUILD/firmware.bin" "$OUT/bus-display-firmware.bin"
ls -l "$OUT/bus-display-firmware.bin"
