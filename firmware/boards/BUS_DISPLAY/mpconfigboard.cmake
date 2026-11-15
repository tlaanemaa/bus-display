# Custom MicroPython board for the bus display (see CLAUDE.md "Custom firmware").
# ESP32_GENERIC (MicroPython v1.28.0) minus Bluetooth, plus the app frozen in.
set(IDF_TARGET esp32)

# Same as ESP32_GENERIC's SDKCONFIG_DEFAULTS, except boards/sdkconfig.ble is
# left out (Bluetooth off: the stock build reserves the BT controller's RAM and
# never releases it), and our own fragment comes last.
set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    ${MICROPY_BOARD_DIR}/sdkconfig.board
)

set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/manifest.py)
