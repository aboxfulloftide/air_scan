# ESP32 Scanner — Setup & Configuration Guide

Each ESP32 acts as a fixed-location passive WiFi scanner. It sniffs beacon and
probe-request frames in promiscuous mode, buffers observations in RAM, then
reconnects to WiFi every 60 seconds to POST the data to the API. Each
observation includes a `probe_count` — the number of raw packets seen for that
device during the 10-second window — so the server can measure true probe
activity without needing access to raw packet streams.

---

## Hardware

- Any standard ESP32 Dev Module (2.4 GHz only)
- ESP32-C5 for dual-band 2.4 + 5 GHz (see [5 GHz note](#5-ghz-esp32-c5))
- USB cable — **must be a data cable, not charge-only**
- USB power supply or powered USB port for permanent deployment

---

## Software prerequisites (on your config machine)

1. **Arduino IDE** (2.x recommended) — https://www.arduino.cc/en/software
2. **ESP32 board support** — in Arduino IDE:
   - File → Preferences → Additional Boards Manager URLs:
     ```
     https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
     ```
   - Tools → Board → Boards Manager → search `esp32` → install **esp32 by Espressif**
3. **ArduinoJson library** (v6.x, by Benoit Blanchon):
   - Tools → Manage Libraries → search `ArduinoJson` → install

---

## Files

```
scanners/esp32/
├── esp32_scanner.ino   # firmware (do not edit for routine setup)
├── config.h            # everything you need to change per device
└── TODO_5ghz.md        # notes on enabling dual-band on ESP32-C5
```

---

## Configuring a new scanner

All per-device settings live in `config.h`. Copy the directory to your config
machine and edit `config.h` before flashing.

### config.h reference

```cpp
// ── Network ──────────────────────────────────────────────────────────────────
#define WIFI_SSID       "hoth"          // your WiFi network name
#define WIFI_PASS       "p@ssw0rd"      // your WiFi password

// Static IP — set all four to 0.0.0.0 to use DHCP instead
#define STATIC_IP       "192.168.1.29"  // pick a free address for this device
#define STATIC_GW       "192.168.1.1"
#define STATIC_SUBNET   "255.255.255.0"
#define STATIC_DNS      "192.168.1.1"

// ── API server ────────────────────────────────────────────────────────────────
#define API_HOST        "http://192.168.1.22:8002"  // machine running air_scan API
#define API_UPLOAD_PATH "/api/observations/upload"

// ── Scanner identity ──────────────────────────────────────────────────────────
#define SCANNER_NAME    "bedroom32"     // unique name — shows in the database
#define DEVICE_HOSTNAME "bedroom32"     // mDNS hostname (bedroom32.local)
#define SCAN_IFACE      "esp32-wifi"    // interface label stored in DB (leave as-is)

// ── Timing ────────────────────────────────────────────────────────────────────
#define SLOT_SECONDS    10    // snapshot window — must match other scanners
#define CYCLE_SECONDS   60    // channel-hop cycle length
#define FLUSH_SECONDS   60    // how often to POST buffered data to API

// ── Buffer ────────────────────────────────────────────────────────────────────
#define MAX_OBS         500   // max observations held in RAM before oldest dropped

// ── Firmware version ──────────────────────────────────────────────────────────
#define FIRMWARE_VERSION "1.2.0"   // bump when flashing a new build for OTA tracking

// ── Channels ──────────────────────────────────────────────────────────────────
#define DUAL_BAND       0     // 0 = 2.4 GHz only, 1 = dual-band (ESP32-C5 only)
static const uint8_t CHANNELS_24[] = {1,2,3,4,5,6,7,8,9,10,11};
#define NUM_CHANNELS    (sizeof(CHANNELS_24) / sizeof(CHANNELS_24[0]))
```

### Checklist for a new device

- [ ] Set a unique `SCANNER_NAME` and `DEVICE_HOSTNAME` (e.g. `kitchen32`, `garage32`)
- [ ] Set `STATIC_IP` to a free address on your LAN (or clear all four to use DHCP)
- [ ] Confirm `API_HOST` points to the machine running the air_scan API
- [ ] Leave `SLOT_SECONDS` at `10` — all scanners must use the same value
- [ ] Leave `FIRMWARE_VERSION` at the current value unless you changed the firmware

---

## Flashing

1. Open `esp32_scanner.ino` in Arduino IDE (it will auto-open `config.h` as a tab)
2. Edit `config.h` as above
3. Plug in the ESP32 via **data USB cable**
4. Tools → Board → ESP32 Arduino → **ESP32 Dev Module**
   - For ESP32-C5: select **ESP32-C5 Dev Module**
5. Tools → Port → select the COM/tty port that appeared when you plugged in
6. Tools → Upload Speed → **115200**
7. Click **Upload** (right arrow button)
8. Open **Tools → Serial Monitor**, set baud to **115200**

Expected boot output:
```
[BOOT] Air Scan ESP32 Scanner
       Name   : bedroom32
       Buffer : 500 slots
[WIFI] Connected, IP: 192.168.1.29
[NTP] Syncing... OK
[SCAN] Started on channel 1
[HOP] ch2
[1700000010] ch2   | live:4   buf:4
[HOP] ch3
...
[FLUSH] 32 observations — connecting WiFi
[WIFI] Connected, IP: 192.168.1.29
[API] POST 200 — {"inserted":32,"devices":14}
```

If you see `[API] POST 200` the device is fully working.

---

## Serial monitor — reading the output

| Line | Meaning |
|---|---|
| `[BOOT]` | Device just powered on |
| `[WIFI] Connected, IP: x.x.x.x` | Successfully joined WiFi |
| `[WIFI] Connect timeout` | Can't reach the SSID — check signal/credentials |
| `[NTP] Syncing... OK` | Clock synced — timestamps will be correct |
| `[SCAN] Started on channel N` | Promiscuous capture running |
| `[HOP] chN` | Channel changed |
| `[timestamp] chN \| live:X buf:Y` | Every 10s: X devices seen this window, Y total buffered. Each buffered entry includes a probe_count of raw packets per device. |
| `[FLUSH] N observations` | About to upload; WiFi reconnect in progress |
| `[API] POST 200` | Upload succeeded |
| `[API] POST failed` | Can't reach API — check network/API_HOST |
| `[OTA] Up to date` | Firmware check ran, no update needed |

---

## Troubleshooting

**No serial port appears when plugged in**
→ Charge-only USB cable. Swap for a data cable.

**`[WIFI] Connect timeout` at boot**
→ SSID or password wrong in `config.h`, or device is too far from the AP.
→ Power-cycle after moving — static IP may conflict if another device grabbed it.

**`[NTP] Syncing... TIMEOUT`**
→ WiFi connected but no internet. NTP will retry on the next flush cycle.
→ Timestamps will be wrong until NTP succeeds; data still uploads fine.

**`[API] POST failed`**
→ `API_HOST` is wrong, the API server is down, or the device is on a different
subnet. Check the IP and that port 8002 is reachable from that network segment.

**`buf:` count keeps growing, never flushes**
→ WiFi is failing silently. Watch for `Connect timeout` messages.
→ Also check that `FLUSH_SECONDS` hasn't been set unreasonably high.

**Device not appearing in the database after flashing**
→ Wait at least 60 seconds (one full flush interval).
→ Confirm `[API] POST 200` in serial output.
→ Check that `SCANNER_NAME` is unique — duplicate names will merge into one row.

---

## Deployed scanners

| Name | IP | Location | Board |
|---|---|---|---|
| bedroom32 | 192.168.1.29 | Bedroom | ESP32 Dev Module |

Add a row here whenever you deploy a new device.

---

## 5 GHz (ESP32-C5)

The ESP32-C5 supports dual-band WiFi 6 but the firmware currently only scans
2.4 GHz. To enable 5 GHz scanning, see `TODO_5ghz.md` — it requires adding a
`CHANNELS_5[]` array to `config.h` and setting `DUAL_BAND 1`.

---

## OTA updates

The firmware checks for OTA updates every 10 flushes (~10 minutes). The API
endpoint is `GET /api/firmware/check?scanner_name=X&version=Y`. If the API
returns `update_available: true` with a firmware URL, the device downloads and
flashes itself, then reboots. No action needed on your part once deployed.
