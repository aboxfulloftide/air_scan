#pragma once

// ── Network ──────────────────────────────────────────────────────────────────
#define WIFI_SSID       "your-ssid"
#define WIFI_PASS       "your-password"

// Static IP — set all four to use DHCP instead
#define STATIC_IP       "192.168.1.29"
#define STATIC_GW       "192.168.1.1"
#define STATIC_SUBNET   "255.255.255.0"
#define STATIC_DNS      "192.168.1.1"

// ── API server ────────────────────────────────────────────────────────────────
// IP or hostname of the machine running the air_scan API
#define API_HOST        "http://192.168.1.22:8002"
#define API_UPLOAD_PATH "/api/observations/upload"

// ── Scanner identity ──────────────────────────────────────────────────────────
// Must be unique — shows up in the scanners table
#define SCANNER_NAME    "bedroom32"
#define DEVICE_HOSTNAME "bedroom32"
#define SCAN_IFACE      "esp32-wifi"
#define BLE_IFACE       "esp32-ble"

// ── Timing ────────────────────────────────────────────────────────────────────
#define SLOT_SECONDS    10      // snapshot window (must match other scanners)
#define CYCLE_SECONDS   60      // channel-hop cycle
#define FLUSH_SECONDS   60      // how often to POST to API

// ── Observation buffer ────────────────────────────────────────────────────────
// Each entry is ~80 bytes; 500 entries = ~40 KB (fits standard ESP32 320KB DRAM)
#define MAX_OBS         500

// ── BLE scanning ──────────────────────────────────────────────────────────────
// Requires the NimBLE-Arduino library (smaller than the bundled Bluedroid stack).
// Set BLE_ENABLED 0 to build WiFi-only. BLE rows are accumulated per MAC over the
// 10s slot (best RSSI kept) and flushed alongside WiFi rows. Keep BLE_BUF_SIZE
// modest — BLE rows share the 40 KB JSON flush buffer with WiFi observations.
#define BLE_ENABLED     1
#define BLE_BUF_SIZE    200     // max distinct BLE devices tracked per slot
#define BLE_SCAN_INT    160     // scan interval in 0.625ms units (160 = 100ms)
#define BLE_SCAN_WIN    80      // scan window in 0.625ms units (80 = 50ms, 50% duty)

// ── Firmware version ──────────────────────────────────────────────────────────
#define FIRMWARE_VERSION "1.3.0"

// ── Channels ─────────────────────────────────────────────────────────────────
// Standard ESP32: 2.4 GHz only
#define DUAL_BAND       0
static const uint8_t CHANNELS_24[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
#define NUM_CHANNELS    (sizeof(CHANNELS_24) / sizeof(CHANNELS_24[0]))
