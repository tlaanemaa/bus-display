// Custom MicroPython board for the bus display (see CLAUDE.md "Custom firmware").
#define MICROPY_HW_BOARD_NAME "Bus display (ESP32, frozen app)"
#define MICROPY_HW_MCU_NAME "ESP32"

// Bluetooth is compiled out (sdkconfig.ble is not used), so the Python
// bindings must be too.
#define MICROPY_PY_BLUETOOTH (0)

// Pinned ON to work around a build inconsistency: with v1.28.0 +
// espressif/idf:v5.5.1 in Docker, a second relative-path ("../py/...")
// module-scan pass sees t-strings ON while the real compile has them OFF
// (port ROM level is EXTRA_FEATURES, t-strings need FULL), so the module table
// references `mp_module_string` that never gets compiled -> link error. The
// stock ESP32_GENERIC fails the same way here. Turning t-strings on makes every
// pass agree; it costs a little flash and no RAM. We don't use them.
#define MICROPY_PY_TSTRINGS (1)
