#pragma once

// ── Network ──────────────────────────────────────────────────────────────────
#define WIFI_SSID       "your-ssid"
#define WIFI_PASS       "your-password"

// ── API server ────────────────────────────────────────────────────────────────
// IP or hostname of the machine running the air_scan API
#define API_HOST        "http://192.168.1.22:8000"
#define API_UPLOAD_PATH "/api/observations/upload"

// ── Scanner identity ──────────────────────────────────────────────────────────
// Must be unique — shows up in the scanners table
#define SCANNER_NAME    "esp32-static-1"
#define SCAN_IFACE      "esp32-wifi"

// ── Timing ────────────────────────────────────────────────────────────────────
#define SLOT_SECONDS    10      // snapshot window (must match other scanners)
#define CYCLE_SECONDS   60      // channel-hop cycle
#define FLUSH_SECONDS   60      // how often to POST to API

// ── Observation buffer ────────────────────────────────────────────────────────
// Each entry is ~80 bytes; 300 entries = ~24 KB (well within ESP32 RAM)
#define MAX_OBS         300

// ── Firmware version ──────────────────────────────────────────────────────────
#define FIRMWARE_VERSION "1.1.0"

// ── Channels ─────────────────────────────────────────────────────────────────
// ESP32-C5: dual-band 2.4 + 5 GHz
// Cycle splits evenly: first half on 2.4 GHz, second half on 5 GHz
static const uint8_t CHANNELS_24[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
#define NUM_CHANNELS    (sizeof(CHANNELS_24) / sizeof(CHANNELS_24[0]))

static const uint8_t CHANNELS_5[]  = {36, 40, 44, 48, 149, 153, 157, 161, 165};
#define NUM_CHANNELS_5  (sizeof(CHANNELS_5)  / sizeof(CHANNELS_5[0]))
