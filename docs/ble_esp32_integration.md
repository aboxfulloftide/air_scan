# BLE scanning on ESP32 — integration guide

How to add Bluetooth Low Energy scanning to the existing ESP32 firmware so its
observations flow through the same API + DB pipeline that the stationary Pi
scanners and the mobile scanner already use.

## Current state (as of this doc)

- **Pi scanners** (`scanners/wifi_scanner.py`) — WiFi + BLE via `bleak` on `hci0`.
  Writes directly to MySQL.
- **Mobile scanner** (`scanners/mobile_scanner.py` + `mobile_ble_scanner.py`) —
  WiFi + BLE, writes to `mobile_observations`, later synced.
- **ESP32 firmware** (`scanners/esp32/esp32_scanner.ino`) — WiFi only. Posts
  batched JSON to `POST /api/observations/upload`. **No BLE today.**

The DB schema already supports BLE everywhere it needs to:

- `devices.device_type` ENUM includes `'BLE'` (`migrate_010_ble.sql`).
- `observations` has `manufacturer_data`, `adv_services`, `adv_service_data`,
  `tx_power`, `tracker_type` (`migrate_017_ble_observations.sql`).
- `mobile_observations` has the same BLE columns (`migrate_010_ble.sql`).

So the work is: (1) make the ESP32 capture BLE advertisements; (2) extend the
`/api/observations/upload` endpoint to accept BLE rows; (3) classify trackers
server-side using the existing `ble_classify.py`.

## Hardware

Any ESP32 variant with a BLE radio works. The radio is shared between WiFi
and BLE at the controller level but the IDF/Arduino BLE stack interleaves
scan windows with WiFi automatically, so concurrent operation is supported.

- **ESP32-C5** (current target in `scanners/esp32/`): BLE 5.0 — fine.
- **ESP32-S3**: BLE 5.0 — fine.
- **ESP32 / ESP32-C3 / ESP32-C6**: all have BLE — fine.

No extra hardware needed.

## ESP32 firmware changes

Use the **NimBLE-Arduino** library (smaller flash + RAM than the bundled
Bluedroid stack — important when running WiFi promisc too).

```cpp
#include <NimBLEDevice.h>
```

### Scan callback

Run BLE in passive scan mode with a callback per advertisement. Buffer
results into a ring buffer the same way WiFi observations are buffered today.

```cpp
struct BleObs {
    uint8_t  mac[6];
    int8_t   rssi;
    int8_t   tx_power;        // INT8_MIN if absent
    uint8_t  random_addr;     // 1 if RPA / static random, 0 if public
    time_t   recorded_at;
    char     mfr_data[160];   // "XXXX:<hex>[,XXXX:<hex>]..."  (truncated)
    char     adv_services[160];   // comma-separated 128-bit UUIDs
    char     svc_data[160];       // "UUID:<hex>[,...]"
    char     local_name[32];
};

static BleObs ble_buffer[BLE_BUF_SIZE];
static int    ble_count = 0;
static portMUX_TYPE ble_mux = portMUX_INITIALIZER_UNLOCKED;

class BleCb : public NimBLEAdvertisedDeviceCallbacks {
    void onResult(NimBLEAdvertisedDevice* dev) override {
        portENTER_CRITICAL(&ble_mux);
        if (ble_count < BLE_BUF_SIZE) {
            BleObs &o = ble_buffer[ble_count++];
            memcpy(o.mac, dev->getAddress().getNative(), 6);
            o.rssi        = dev->getRSSI();
            o.tx_power    = dev->haveTXPower() ? dev->getTXPower() : INT8_MIN;
            o.random_addr = (dev->getAddressType() != BLE_ADDR_PUBLIC) ? 1 : 0;
            time(&o.recorded_at);
            // ... format mfr_data, adv_services, svc_data, local_name
        }
        portEXIT_CRITICAL(&ble_mux);
    }
};

void setup_ble() {
    NimBLEDevice::init("");
    NimBLEScan* s = NimBLEDevice::getScan();
    s->setAdvertisedDeviceCallbacks(new BleCb(), /*wantDuplicates=*/true);
    s->setActiveScan(true);      // request scan responses (gets local_name, tx_power)
    s->setInterval(160);         // 100ms
    s->setWindow(80);            // 50ms — 50% duty cycle leaves time for WiFi
    s->start(0, nullptr, false); // duration=0 → run forever
}
```

`wantDuplicates=true` is important: a single BLE advertiser emits the same
payload repeatedly, and we want to update RSSI / `last_heard` on every one,
not just the first.

### Coexistence with WiFi sniffing

The ESP32 firmware currently spends most time in WiFi promiscuous mode and
only enables station mode briefly to flush. BLE scanning works fine in
parallel with promisc — the controller schedules both. Two things to verify
in testing:

1. Channel hopping in WiFi promisc isn't disrupted (it shouldn't be, BLE
   uses 3 advertising channels disjoint from the WiFi data path).
2. Heap usage stays in budget. NimBLE adds ~25 KB. The existing JSON buffer
   is already 40 KB. Watch `ESP.getFreeHeap()` in the bench log.

If memory is tight on the C5, drop the WiFi JSON buffer or move BLE
serialization to the same 40 KB buffer (BLE batch flushes alongside WiFi).

### Flushing BLE observations

Reuse the existing flush path. When `flush_observations()` builds the
`observations` array, append BLE rows after the WiFi rows. Each BLE row
looks like:

```json
{
  "mac":              "aa:bb:cc:dd:ee:ff",
  "device_type":      "BLE",
  "signal_dbm":       -72,
  "recorded_at":      "2026-06-12T14:05:33",
  "interface":        "esp32-ble",
  "is_randomized":    true,
  "tx_power":         -8,
  "manufacturer_data":"004C:12abcd...",
  "adv_services":     "0000feaa-0000-1000-8000-00805f9b34fb",
  "adv_service_data": "0000feaa-...:40ab12...",
  "local_name":       "Tag-7F2A"
}
```

Channel / `freq_mhz` are NULL for BLE rows. `ssid` is NULL. The server fills
in `tracker_type` from `manufacturer_data` + `adv_services` + `adv_service_data`.

## Server changes

`api/observations/router.py:upload_observations` currently assumes WiFi:

1. **`_build_obs_values` does not pass BLE columns.** Extend it to set
   `manufacturer_data`, `adv_services`, `adv_service_data`, `tx_power`,
   `tracker_type` (all nullable), and to allow `interface` like `esp32-ble`.

2. **`device_type` is hard-defaulted to "Client".** Accept `"BLE"` and pass it
   through to the `devices` upsert (the ENUM already allows it).

3. **Random-address detection differs.** Current code does
   `int(mac.split(":")[0], 16) & 0x02` — that's the WiFi locally-administered
   bit. For BLE, randomized = `& 0x40` of the first byte (see
   `wifi_scanner.py:393`). Branch on `device_type` or trust the
   `is_randomized` flag the client sends.

4. **Tracker classification.** Call `ble_classify.classify_tracker()` server-
   side after parsing the BLE-specific fields. Single source of truth — same
   classifier the Pi and mobile scanners use — and lets us evolve the rules
   without reflashing ESP32s. Inputs the function expects:
   - `manufacturer_data`: `{cid_int: bytes}` — parse from
     `"XXXX:<hex>,..."` form.
   - `service_uuids`: list of lowercase UUID strings — split `adv_services`
     on commas.
   - `service_data`: `{uuid_str: bytes}` — parse from `"UUID:<hex>,..."` form.

Sketch of the parser helper to put alongside `_build_obs_values`:

```python
def _parse_ble_fields(obs):
    mfr = {}
    for entry in (obs.get("manufacturer_data") or "").split(","):
        if ":" in entry:
            cid_hex, payload = entry.split(":", 1)
            try:
                mfr[int(cid_hex, 16)] = bytes.fromhex(payload)
            except ValueError:
                pass
    svcs = [u.strip().lower() for u in
            (obs.get("adv_services") or "").split(",") if u.strip()]
    svc_data = {}
    for entry in (obs.get("adv_service_data") or "").split(","):
        if ":" in entry:
            uuid, payload = entry.split(":", 1)
            try:
                svc_data[uuid.lower()] = bytes.fromhex(payload)
            except ValueError:
                pass
    return mfr, svcs, svc_data
```

The hex on-wire format matches what `wifi_scanner.py:407` and
`mobile_ble_scanner.py` already produce, so the strings can be stored
verbatim — no re-encoding for the DB columns.

## Why send raw advertisement bytes (and not classify on-device)

Three reasons:

1. **`ble_classify.py` evolves** — new tracker vendors appear, frame types
   are reverse-engineered. Reflashing every ESP32 each time is operationally
   expensive.
2. **The `observations` columns store the raw fields anyway.** Even when the
   Pi classifies locally, the manufacturer / service-data hex is persisted
   so we can re-classify offline.
3. **Flash + RAM cost.** The classifier is small but the supporting tables
   would grow over time.

The ESP32 should not import or duplicate `ble_classify.py`.

## OUI / `manufacturer` lookup

The `_oui_lookup()` helper in `observations/router.py` uses scapy's
`manufdb`, which is WiFi-MAC-OUI-only. For BLE, populate `manufacturer`
from the **Bluetooth SIG company ID** in `manufacturer_data` instead (e.g.
`0x004C → "Apple"`, `0x0075 → "Samsung"`). Either:

- Add a small Bluetooth SIG CID → name table to the server (preferred —
  the list is short for the vendors we care about).
- Or skip `manufacturer` for BLE rows and rely on `tracker_type` for
  recognizable devices.

## Testing checklist

- [ ] NimBLE compiles into firmware without exceeding flash budget.
- [ ] `BLE-only` test build (no WiFi promisc) flushes BLE rows through the
      API, rows land in `observations` with `device_type='BLE'` and at least
      one populated BLE column.
- [ ] Tracker classification round-trip: walk an AirTag past the ESP32 → row
      appears with `tracker_type='Apple:FindMy'`.
- [ ] Concurrent run with WiFi promisc — observation counts for both
      stay in normal range over a 30-minute window.
- [ ] `is_randomized` matches expectation (most modern phones / trackers
      rotate addresses → `1`).

## Out of scope for this doc

- Mesh / BLE-relay scanner topology.
- BLE active scan response filtering (we leave active scan on to grab
  `local_name` and `tx_power`).
- Long-term storage of full raw advertisement payloads — only the parsed
  fields go in the DB today.
