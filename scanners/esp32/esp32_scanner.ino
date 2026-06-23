/*
 * Air Scan — ESP32 Static Scanner
 *
 * Passively captures 802.11 beacon and probe-request frames on 2.4 GHz.
 * Tracks best RSSI per MAC per 10-second window (same slot boundaries as
 * the Pi scanners). Every FLUSH_SECONDS, reconnects to WiFi and POSTs the
 * buffered observations to the API.
 *
 * Required libraries (install via Arduino Library Manager):
 *   - ArduinoJson    (Benoit Blanchon, v6.x)
 *   - NimBLE-Arduino (h2zero, v1.4.x) — only when BLE_ENABLED (config.h)
 *
 * ── Flash settings (arduino-cli) ─────────────────────────────────────────────
 * Board FQBN:  esp32:esp32:esp32c5:CDCOnBoot=cdc
 *
 *   CDCOnBoot=cdc   — REQUIRED. Without this Serial.print() is silent over USB
 *                     and the device appears dead after flashing.
 *
 * Example compile + upload:
 *   arduino-cli compile --fqbn esp32:esp32:esp32c5:CDCOnBoot=cdc <sketch-dir>
 *   arduino-cli upload  --fqbn esp32:esp32:esp32c5:CDCOnBoot=cdc \
 *                       --port /dev/ttyACM0 <sketch-dir>
 *
 * Note: arduino-cli requires the sketch directory name to match the .ino file
 * name. Copy scanners/esp32/ to a temp dir named "esp32_scanner/" before
 * compiling (the source dir is named "esp32" which doesn't match).
 *
 * ── Dual-band notes (ESP32-C5) ───────────────────────────────────────────────
 * esp_wifi_set_band_mode(WIFI_BAND_MODE_AUTO) must be called after WiFi init
 * or the radio silently ignores 5 GHz channel calls and then stops receiving
 * on 2.4 GHz too. See wifi_disconnect_and_resume().
 *
 * ── Changelog ────────────────────────────────────────────────────────────────
 * v1.3.0 — 2026-06-22
 *   Add BLE advertisement scanning via NimBLE (config: BLE_ENABLED). BLE runs
 *   in active scan mode concurrently with WiFi promisc and is buffered per-MAC
 *   over the same 10s slot (best RSSI kept), then flushed alongside WiFi rows
 *   as device_type="BLE". Raw manufacturer/service-data hex is sent verbatim;
 *   the server classifies tracker_type. JSON flush buffer raised 40KB→64KB to
 *   hold the combined WiFi+BLE payload — verify ESP.getFreeHeap() on target.
 *   NOTE: not yet validated on hardware; see docs/ble_esp32_integration.md.
 *
 * v1.1.2 — 2026-03-22
 *   Fix: serialize JSON into a static char buffer and call doc.clear() before
 *   opening the HTTP connection. Previously DynamicJsonDocument (40KB) and
 *   String payload (~30KB) were live simultaneously, causing heap fragmentation
 *   that crashed the device after ~8 hours. Peak heap is now ~40KB instead of
 *   ~70KB, and the static buffer doesn't fragment the allocator.
 *
 * v1.1.1 — 2026-03-19
 *   Fix: esp_wifi_set_promiscuous_rx_cb() must be re-called inside the
 *   band-change block of hop_channel(). On ESP32-C5, toggling promiscuous
 *   mode (false→true) during a 2.4↔5 GHz band hop silently clears the RX
 *   callback. Symptom: live:0 buf:0 permanently after the first band switch
 *   (~30s into operation). Device never flushes to API.
 *
 * v1.1.0 — 2026-03-17
 *   Add dual-band 2.4+5 GHz scanning (ESP32-C5). Add health stats, bench
 *   timing, NTP throttle. Fix: re-register promiscuous RX callback inside
 *   wifi_disconnect_and_resume() — WiFi mode cycling (NULL→STA) clears it.
 * ─────────────────────────────────────────────────────────────────────────────
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include <ArduinoJson.h>
#include <esp_wifi.h>
#include <time.h>
#include "config.h"

#if BLE_ENABLED
#include <NimBLEDevice.h>
#endif

// ── Observation structs ───────────────────────────────────────────────────────

struct LiveEntry {
    uint8_t  mac[6];
    int8_t   signal;
    uint8_t  channel;
    uint16_t freq_mhz;
    char     ssid[33];
    uint8_t  device_type;   // 0=AP 1=Client
    bool     ht, vht;
    bool     active;
    uint16_t probe_count;   // raw packets seen this window
    time_t   slot_ts;
};

struct ObsEntry {
    uint8_t  mac[6];
    int8_t   signal;
    uint8_t  channel;
    uint16_t freq_mhz;
    char     ssid[33];
    uint8_t  device_type;
    bool     ht, vht;
    uint16_t probe_count;   // raw packets seen during the 10s window
    time_t   recorded_at;
};

static LiveEntry live[MAX_OBS];
static ObsEntry  obs_buffer[MAX_OBS];
static int       obs_count  = 0;
static int       live_count = 0;

static portMUX_TYPE buf_mux = portMUX_INITIALIZER_UNLOCKED;

static uint8_t   current_channel = 1;
static time_t    last_flush_ts   = 0;
static time_t    last_slot_ts    = 0;
static bool      time_synced     = false;
static uint32_t  flush_count     = 0;

// ── BLE scanning ───────────────────────────────────────────────────────────────
#if BLE_ENABLED

// Live window (one entry per distinct BLE address seen this slot) and the
// snapshot buffer flushed to the API. Mirrors the WiFi live→obs model so BLE
// observations land on the same 10s slot boundaries as everything else.
struct BleLive {
    char    mac[18];          // "aa:bb:cc:dd:ee:ff"
    int8_t  signal;
    int8_t  tx_power;         // INT8_MIN if not advertised
    bool    random_addr;
    bool    active;
    char    mfr_data[160];    // "XXXX:<hex>[,...]" (truncated)
    char    adv_services[160];// comma-separated 128-bit UUIDs
    char    svc_data[160];    // "uuid:<hex>[,...]"
};

struct BleObs {
    char    mac[18];
    int8_t  signal;
    int8_t  tx_power;
    bool    random_addr;
    time_t  recorded_at;
    char    mfr_data[160];
    char    adv_services[160];
    char    svc_data[160];
};

static BleLive      ble_live[BLE_BUF_SIZE];
static BleObs       ble_obs[BLE_BUF_SIZE];
static int          ble_live_count = 0;
static int          ble_obs_count  = 0;
static portMUX_TYPE ble_mux = portMUX_INITIALIZER_UNLOCKED;

// Hex-encode bytes into a NUL-terminated lowercase string.
static void bytes_to_hex(const uint8_t *data, size_t len, char *out, size_t out_sz) {
    size_t j = 0;
    for (size_t i = 0; i < len && j + 3 <= out_sz; i++) {
        snprintf(out + j, out_sz - j, "%02x", data[i]);
        j += 2;
    }
    out[j] = '\0';
}

static int ble_find_live(const char *mac) {
    for (int i = 0; i < ble_live_count; i++) {
        if (ble_live[i].active && strncmp(ble_live[i].mac, mac, 18) == 0)
            return i;
    }
    return -1;
}

// NimBLE host-task callback: one call per received advertisement. We format the
// advertisement fields into local buffers first (snprintf is too slow to hold a
// spinlock across), then take the lock only to insert/update the live entry.
class BleCb : public NimBLEAdvertisedDeviceCallbacks {
    void onResult(NimBLEAdvertisedDevice *dev) override {
        char mac[18];
        strncpy(mac, dev->getAddress().toString().c_str(), sizeof(mac));
        mac[17] = '\0';

        int8_t rssi    = (int8_t)dev->getRSSI();
        int8_t txp     = dev->haveTXPower() ? (int8_t)dev->getTXPower() : INT8_MIN;
        bool   is_rand = (dev->getAddress().getType() != BLE_ADDR_PUBLIC);

        // Manufacturer data: "XXXX:<hex>" — first 2 bytes are the SIG company
        // ID (little-endian), the rest is the payload.
        char mfr[160] = {};
        if (dev->haveManufacturerData()) {
            std::string md = dev->getManufacturerData();
            if (md.size() >= 2) {
                uint16_t cid = (uint8_t)md[0] | ((uint8_t)md[1] << 8);
                char hex[150];
                bytes_to_hex((const uint8_t *)md.data() + 2, md.size() - 2,
                             hex, sizeof(hex));
                snprintf(mfr, sizeof(mfr), "%04X:%s", cid, hex);
            }
        }

        // Advertised service UUIDs (full 128-bit form, comma-separated).
        char svcs[160] = {};
        for (int i = 0; i < dev->getServiceUUIDCount(); i++) {
            std::string u = dev->getServiceUUID(i).to128().toString();
            size_t cur = strlen(svcs);
            snprintf(svcs + cur, sizeof(svcs) - cur, "%s%s",
                     cur ? "," : "", u.c_str());
        }

        // Service data: "uuid:<hex>" entries, comma-separated.
        char svcd[160] = {};
        for (int i = 0; i < dev->getServiceDataCount(); i++) {
            std::string u = dev->getServiceDataUUID(i).to128().toString();
            std::string d = dev->getServiceData(i);
            char hex[120];
            bytes_to_hex((const uint8_t *)d.data(), d.size(), hex, sizeof(hex));
            size_t cur = strlen(svcd);
            snprintf(svcd + cur, sizeof(svcd) - cur, "%s%s:%s",
                     cur ? "," : "", u.c_str(), hex);
        }

        portENTER_CRITICAL(&ble_mux);
        int idx = ble_find_live(mac);
        if (idx < 0) {
            if (ble_live_count >= BLE_BUF_SIZE) { portEXIT_CRITICAL(&ble_mux); return; }
            idx = ble_live_count++;
            ble_live[idx].active = true;
            strncpy(ble_live[idx].mac, mac, 18);
            ble_live[idx].signal      = rssi;
            ble_live[idx].tx_power    = txp;
            ble_live[idx].random_addr = is_rand;
            strncpy(ble_live[idx].mfr_data,     mfr,  sizeof(ble_live[idx].mfr_data));
            strncpy(ble_live[idx].adv_services, svcs, sizeof(ble_live[idx].adv_services));
            strncpy(ble_live[idx].svc_data,     svcd, sizeof(ble_live[idx].svc_data));
        } else {
            // Keep the strongest RSSI in the window; backfill any field that a
            // later (e.g. scan-response) advertisement fills in.
            if (rssi > ble_live[idx].signal) ble_live[idx].signal = rssi;
            if (txp != INT8_MIN) ble_live[idx].tx_power = txp;
            if (mfr[0]  && !ble_live[idx].mfr_data[0])
                strncpy(ble_live[idx].mfr_data, mfr, sizeof(ble_live[idx].mfr_data));
            if (svcs[0] && !ble_live[idx].adv_services[0])
                strncpy(ble_live[idx].adv_services, svcs, sizeof(ble_live[idx].adv_services));
            if (svcd[0] && !ble_live[idx].svc_data[0])
                strncpy(ble_live[idx].svc_data, svcd, sizeof(ble_live[idx].svc_data));
        }
        portEXIT_CRITICAL(&ble_mux);
    }
};

static void setup_ble() {
    NimBLEDevice::init("");
    NimBLEScan *s = NimBLEDevice::getScan();
    s->setAdvertisedDeviceCallbacks(new BleCb(), /*wantDuplicates=*/true);
    s->setActiveScan(true);              // request scan responses (tx_power, name)
    s->setInterval(BLE_SCAN_INT);
    s->setWindow(BLE_SCAN_WIN);
    s->start(0, nullptr, false);         // duration 0 → scan forever
    Serial.println("[BLE] Scanning started");
}

// Commit the BLE live window into the snapshot buffer at a slot boundary.
static void take_ble_snapshot(time_t slot_ts) {
    portENTER_CRITICAL(&ble_mux);
    for (int i = 0; i < ble_live_count; i++) {
        if (!ble_live[i].active) continue;
        if (ble_obs_count >= BLE_BUF_SIZE) break;
        BleObs &o = ble_obs[ble_obs_count++];
        strncpy(o.mac, ble_live[i].mac, 18);
        o.signal      = ble_live[i].signal;
        o.tx_power    = ble_live[i].tx_power;
        o.random_addr = ble_live[i].random_addr;
        o.recorded_at = slot_ts;
        strncpy(o.mfr_data,     ble_live[i].mfr_data,     sizeof(o.mfr_data));
        strncpy(o.adv_services, ble_live[i].adv_services, sizeof(o.adv_services));
        strncpy(o.svc_data,     ble_live[i].svc_data,     sizeof(o.svc_data));
    }
    ble_live_count = 0;
    portEXIT_CRITICAL(&ble_mux);
}
#endif  // BLE_ENABLED

// ── 802.11 frame parsing ──────────────────────────────────────────────────────

#define FRAME_TYPE_MGMT   0
#define SUBTYPE_PROBE_REQ 4
#define SUBTYPE_BEACON    8

static inline uint8_t frame_type(const uint8_t *fc)    { return (fc[0] >> 2) & 0x03; }
static inline uint8_t frame_subtype(const uint8_t *fc) { return (fc[0] >> 4) & 0x0F; }

static void parse_ies(const uint8_t *body, int body_len,
                      char *ssid_out, bool *ht_out, bool *vht_out)
{
    int i = 0;
    while (i + 1 < body_len) {
        uint8_t id  = body[i];
        uint8_t len = body[i + 1];
        if (i + 2 + len > body_len) break;

        if (id == 0 && ssid_out && len > 0 && len <= 32) {
            memcpy(ssid_out, body + i + 2, len);
            ssid_out[len] = '\0';
        } else if (id == 45) {
            *ht_out  = true;
        } else if (id == 191) {
            *vht_out = true;
        }
        i += 2 + len;
    }
}

static uint8_t channel_to_freq_hi(uint8_t ch) {
    // Returns upper byte of 2.4 GHz channel frequency (lower byte is always computed)
    return 0;  // not needed — just use ch * 5 + 2407 formula
}

static uint16_t channel_to_freq(uint8_t ch) {
    if (ch >= 1 && ch <= 13) return 2407 + ch * 5;
    if (ch == 14)             return 2484;
    if (ch >= 36)             return 5000 + ch * 5;
    return 0;
}

// ── Live window management ────────────────────────────────────────────────────

static int find_live(const uint8_t *mac) {
    for (int i = 0; i < live_count; i++) {
        if (live[i].active && memcmp(live[i].mac, mac, 6) == 0)
            return i;
    }
    return -1;
}

static int alloc_live(const uint8_t *mac) {
    if (live_count < MAX_OBS) {
        memcpy(live[live_count].mac, mac, 6);
        live[live_count].active = true;
        return live_count++;
    }
    return -1;
}

// ── Snapshot: commit live window to obs_buffer ────────────────────────────────

static void take_snapshot(time_t slot_ts) {
    portENTER_CRITICAL(&buf_mux);
    for (int i = 0; i < live_count; i++) {
        if (!live[i].active) continue;
        if (obs_count >= MAX_OBS) break;

        ObsEntry &o = obs_buffer[obs_count++];
        memcpy(o.mac, live[i].mac, 6);
        o.signal      = live[i].signal;
        o.channel     = live[i].channel;
        o.freq_mhz    = live[i].freq_mhz;
        o.device_type = live[i].device_type;
        o.ht          = live[i].ht;
        o.vht         = live[i].vht;
        o.probe_count = live[i].probe_count;
        o.recorded_at = slot_ts;
        strncpy(o.ssid, live[i].ssid, 32);
        o.ssid[32] = '\0';
    }
    // Clear live window for next slot
    live_count = 0;
    portEXIT_CRITICAL(&buf_mux);
}

// ── Promiscuous packet callback ────────────────────────────────────────────────

static void IRAM_ATTR pkt_callback(void *buf, wifi_promiscuous_pkt_type_t type) {
    if (type != WIFI_PKT_MGMT) return;

    const wifi_promiscuous_pkt_t *pkt = (wifi_promiscuous_pkt_t *)buf;
    const uint8_t *payload = pkt->payload;
    int pkt_len = pkt->rx_ctrl.sig_len;
    if (pkt_len < 24) return;

    const uint8_t *fc = payload;
    if (frame_type(fc) != FRAME_TYPE_MGMT) return;

    uint8_t subtype = frame_subtype(fc);
    if (subtype != SUBTYPE_BEACON && subtype != SUBTYPE_PROBE_REQ) return;

    // Broadcast / multicast probe requests carry no useful source — skip ff:ff:...
    const uint8_t *src_mac = (subtype == SUBTYPE_BEACON)
                              ? payload + 16   // addr3 = BSSID
                              : payload + 10;  // addr2 = source

    bool is_broadcast = true;
    for (int i = 0; i < 6; i++) {
        if (src_mac[i] != 0xff) { is_broadcast = false; break; }
    }
    if (is_broadcast) return;

    int8_t  rssi    = pkt->rx_ctrl.rssi;
    uint8_t chan    = pkt->rx_ctrl.channel;
    uint16_t freq   = channel_to_freq(chan);

    char    ssid[33] = {};
    bool    ht = false, vht = false;
    uint8_t dev_type = (subtype == SUBTYPE_BEACON) ? 0 : 1;

    // Parse IEs — body starts at byte 24 for beacon (skip 12-byte fixed params), 24 for probe req
    int body_offset = 24;
    if (subtype == SUBTYPE_BEACON) body_offset += 12;  // timestamp(8) + interval(2) + caps(2)
    if (body_offset < pkt_len) {
        parse_ies(payload + body_offset, pkt_len - body_offset, ssid, &ht, &vht);
    }

    portENTER_CRITICAL(&buf_mux);
    int idx = find_live(src_mac);
    if (idx < 0) {
        idx = alloc_live(src_mac);
        if (idx < 0) { portEXIT_CRITICAL(&buf_mux); return; }
        live[idx].signal      = rssi;
        live[idx].channel     = chan;
        live[idx].freq_mhz    = freq;
        live[idx].device_type = dev_type;
        live[idx].ht          = ht;
        live[idx].vht         = vht;
        live[idx].probe_count = 1;
        strncpy(live[idx].ssid, ssid, 32);
    } else {
        live[idx].probe_count++;
        // Keep highest RSSI in window
        if (rssi > live[idx].signal) {
            live[idx].signal   = rssi;
            live[idx].channel  = chan;
            live[idx].freq_mhz = freq;
        }
        if (ht)  live[idx].ht  = true;
        if (vht) live[idx].vht = true;
        if (ssid[0] && !live[idx].ssid[0])
            strncpy(live[idx].ssid, ssid, 32);
    }
    portEXIT_CRITICAL(&buf_mux);
}

// ââ Channel hopping ââââââââââââââââââââââââââââââââââââââââ

static uint8_t pick_channel(time_t now) {
    int slot  = (int)(now % CYCLE_SECONDS) / SLOT_SECONDS;
    int cycle = (int)(now / CYCLE_SECONDS);

#if DUAL_BAND
    // Mirrors the Pi scanner's build_schedule() dual-band split.
    // First half of CYCLE_SECONDS → 2.4 GHz, second half → 5 GHz.
    int half = (CYCLE_SECONDS / SLOT_SECONDS) / 2;
    if (slot < half) {
        return CHANNELS_24[(cycle * half + slot) % NUM_CHANNELS];
    } else {
        int s = slot - half;
        return CHANNELS_5[(cycle * half + s) % NUM_CHANNELS_5];
    }
#else
    // 2.4 GHz only — use full cycle across all channels
    int total = CYCLE_SECONDS / SLOT_SECONDS;
    return CHANNELS_24[(cycle * total + slot) % NUM_CHANNELS];
#endif
}

static void hop_channel(time_t now) {
    uint8_t ch = pick_channel(now);
    if (ch != current_channel) {
        bool band_change = (ch >= 36) != (current_channel >= 36);
        if (band_change) {
            // Radio needs promiscuous restart when crossing bands on ESP32-C5.
            // Must re-register callback — toggling promiscuous clears it on C5.
            esp_wifi_set_promiscuous(false);
            esp_wifi_set_band_mode(WIFI_BAND_MODE_AUTO);
            esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
            esp_wifi_set_promiscuous_rx_cb(pkt_callback);
            esp_wifi_set_promiscuous(true);
        } else {
            esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
        }
        current_channel = ch;
        Serial.printf("[HOP] ch%d\n", ch);
    }
}

// ── WiFi connect / disconnect ──────────────────────────────────────────────────

static bool wifi_connect() {
    esp_wifi_set_promiscuous(false);

    WiFi.setHostname(DEVICE_HOSTNAME);

    IPAddress ip, gw, sn, dns;
    if (ip.fromString(STATIC_IP) && gw.fromString(STATIC_GW) &&
        sn.fromString(STATIC_SUBNET) && dns.fromString(STATIC_DNS)) {
        WiFi.config(ip, gw, sn, dns);
    }

    WiFi.begin(WIFI_SSID, WIFI_PASS);
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - start > 15000) {
            Serial.println("[WIFI] Connect timeout");
            return false;
        }
        delay(200);
    }
    Serial.printf("[WIFI] Connected, IP: %s\n", WiFi.localIP().toString().c_str());
    return true;
}

static void wifi_disconnect_and_resume() {
    // Fully tear down managed-mode WiFi before switching to promiscuous.
    // On ESP32-C5 the radio needs a clean stop/start cycle between modes.
    esp_wifi_set_promiscuous(false);
    WiFi.disconnect(false);
    WiFi.mode(WIFI_MODE_NULL);
    delay(100);
    WiFi.mode(WIFI_STA);
    delay(100);
    esp_wifi_set_band_mode(WIFI_BAND_MODE_AUTO);  // Enable 2.4+5 GHz (required on ESP32-C5)

    // Explicitly accept management frames (required on ESP32-C5)
    wifi_promiscuous_filter_t filter = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT
    };
    esp_wifi_set_promiscuous_filter(&filter);

    // Re-register callback — WiFi mode cycling (WIFI_MODE_NULL→STA) clears it
    esp_wifi_set_promiscuous_rx_cb(pkt_callback);

    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(current_channel, WIFI_SECOND_CHAN_NONE);
}

// ── NTP sync ──────────────────────────────────────────────────────────────────

static bool sync_ntp() {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    Serial.print("[NTP] Syncing");
    unsigned long start = millis();
    struct tm ti;
    while (!getLocalTime(&ti)) {
        if (millis() - start > 10000) {
            Serial.println(" TIMEOUT");
            return false;
        }
        Serial.print(".");
        delay(500);
    }
    Serial.println(" OK");
    return true;
}

// ── OTA update check ──────────────────────────────────────────────────────────

static void check_ota() {
    HTTPClient http;
    String url = String(API_HOST) + "/api/firmware/check?scanner_name="
                 + SCANNER_NAME + "&version=" + FIRMWARE_VERSION;
    http.begin(url);
    http.setTimeout(8000);
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[OTA] Check failed: HTTP %d\n", code);
        http.end();
        return;
    }

    DynamicJsonDocument doc(512);
    DeserializationError err = deserializeJson(doc, http.getString());
    http.end();
    if (err || !doc["update_available"].as<bool>()) {
        Serial.println("[OTA] Up to date");
        return;
    }

    String fw_url = doc["url"].as<String>();
    Serial.printf("[OTA] Update available → %s\n", fw_url.c_str());

    WiFiClient client;
    t_httpUpdate_return ret = httpUpdate.update(client, fw_url);
    // HTTP_UPDATE_OK causes automatic reboot; only failure lands here
    if (ret == HTTP_UPDATE_FAILED) {
        Serial.printf("[OTA] Failed (%d): %s\n",
                      httpUpdate.getLastError(),
                      httpUpdate.getLastErrorString().c_str());
    }
}

// ── HTTP flush ────────────────────────────────────────────────────────────────

static void flush_to_api() {
    int ble_count = 0;
#if BLE_ENABLED
    ble_count = ble_obs_count;
#endif
    if (obs_count == 0 && ble_count == 0) return;

    Serial.printf("[FLUSH] %d wifi + %d ble observations — connecting WiFi\n",
                  obs_count, ble_count);

    unsigned long t0 = millis();

    if (!wifi_connect()) {
        wifi_disconnect_and_resume();
        return;
    }

    // NTP sync every 300 flushes (~5 hours at 60s flush interval)
    flush_count++;
    if (flush_count % 300 == 1) sync_ntp();  // sync on first flush, then every 5h

    unsigned long t_wifi = millis();

    // Collect health stats while WiFi is up (macAddress() needs STA mode)
    uint32_t h_free_heap     = ESP.getFreeHeap();
    uint32_t h_min_free_heap = ESP.getMinFreeHeap();
    unsigned long h_uptime   = millis();
    float h_temp             = temperatureRead();
    String h_mac             = WiFi.macAddress();

    // Build JSON payload
    // WiFi obs ~120 bytes each; BLE obs ~250 bytes each (raw adv hex). Buffer is
    // 64 KB to hold the combined WiFi+BLE batch. Freed (doc.clear) before the
    // HTTP connect so peak heap is transient — watch ESP.getFreeHeap() on target
    // and trim MAX_OBS / BLE_BUF_SIZE if the C5 runs short.
    DynamicJsonDocument doc(65536);
    doc["scanner_host"] = SCANNER_NAME;

    JsonObject health = doc.createNestedObject("health");
    health["mac"]           = h_mac;
    health["free_heap"]     = h_free_heap;
    health["min_free_heap"] = h_min_free_heap;
    health["uptime_ms"]     = h_uptime;
    health["temperature_c"] = serialized(String(h_temp, 1));

    JsonArray arr = doc.createNestedArray("observations");

    portENTER_CRITICAL(&buf_mux);
    int count = obs_count;
    portEXIT_CRITICAL(&buf_mux);

    for (int i = 0; i < count; i++) {
        ObsEntry &o = obs_buffer[i];
        JsonObject obj = arr.createNestedObject();

        char mac_str[18];
        snprintf(mac_str, sizeof(mac_str), "%02x:%02x:%02x:%02x:%02x:%02x",
                 o.mac[0], o.mac[1], o.mac[2], o.mac[3], o.mac[4], o.mac[5]);

        // Format timestamp as ISO8601
        struct tm *t = gmtime(&o.recorded_at);
        char ts_str[20];
        strftime(ts_str, sizeof(ts_str), "%Y-%m-%dT%H:%M:%S", t);

        obj["mac"]         = mac_str;
        obj["device_type"] = (o.device_type == 0) ? "AP" : "Client";
        obj["signal_dbm"]  = o.signal;
        obj["channel"]     = o.channel;
        obj["freq_mhz"]    = o.freq_mhz;
        obj["ssid"]        = o.ssid;
        obj["ht"]          = o.ht;
        obj["vht"]         = o.vht;
        obj["he"]          = false;
        obj["probe_count"] = o.probe_count;
        obj["interface"]   = SCAN_IFACE;
        obj["recorded_at"] = ts_str;
    }

#if BLE_ENABLED
    // Append BLE rows after the WiFi rows. channel / freq / ssid are omitted
    // (NULL server-side); the server classifies tracker_type from the raw hex.
    int ble_n;
    portENTER_CRITICAL(&ble_mux);
    ble_n = ble_obs_count;
    portEXIT_CRITICAL(&ble_mux);

    for (int i = 0; i < ble_n; i++) {
        BleObs &o = ble_obs[i];
        JsonObject obj = arr.createNestedObject();

        struct tm *t = gmtime(&o.recorded_at);
        char ts_str[20];
        strftime(ts_str, sizeof(ts_str), "%Y-%m-%dT%H:%M:%S", t);

        obj["mac"]           = o.mac;
        obj["device_type"]   = "BLE";
        obj["signal_dbm"]    = o.signal;
        obj["is_randomized"] = o.random_addr;
        obj["interface"]     = BLE_IFACE;
        obj["recorded_at"]   = ts_str;
        if (o.tx_power != INT8_MIN)  obj["tx_power"]          = o.tx_power;
        if (o.mfr_data[0])           obj["manufacturer_data"] = o.mfr_data;
        if (o.adv_services[0])       obj["adv_services"]      = o.adv_services;
        if (o.svc_data[0])           obj["adv_service_data"]  = o.svc_data;
    }
#endif

    // Serialize into a static buffer (BSS, not heap) then free the doc before
    // opening the HTTP connection. This halves peak heap usage (~40KB → ~40KB
    // instead of doc + String simultaneously) and prevents fragmentation-induced
    // crash after several hours of operation.
    static char json_buf[65536];
    size_t json_len = serializeJson(doc, json_buf, sizeof(json_buf));
    doc.clear();  // free heap before HTTP connect

    unsigned long t_json = millis();

    HTTPClient http;
    String url = String(API_HOST) + API_UPLOAD_PATH;
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(10000);

    int code = http.POST((uint8_t *)json_buf, json_len);

    unsigned long t_post = millis();
    Serial.printf("[BENCH] wifi+ntp=%lums  json=%lums  post=%lums  total=%lums\n",
                  t_wifi - t0, t_json - t_wifi, t_post - t_json, t_post - t0);

    if (code > 0) {
        Serial.printf("[API] POST %d — %s\n", code, http.getString().c_str());
        // Clear buffer only on success
        portENTER_CRITICAL(&buf_mux);
        obs_count = 0;
        portEXIT_CRITICAL(&buf_mux);
#if BLE_ENABLED
        portENTER_CRITICAL(&ble_mux);
        ble_obs_count = 0;
        portEXIT_CRITICAL(&ble_mux);
#endif

        // OTA check every 10 flushes (~10 min)
        if (flush_count % 10 == 0) check_ota();
    } else {
        Serial.printf("[API] POST failed: %s\n", http.errorToString(code).c_str());
    }
    http.end();

    wifi_disconnect_and_resume();
}

// ── Setup ─────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n[BOOT] Air Scan ESP32 Scanner");
    Serial.printf("       Name   : %s\n", SCANNER_NAME);
    Serial.printf("       Buffer : %d slots\n", MAX_OBS);

    memset(live,       0, sizeof(live));
    memset(obs_buffer, 0, sizeof(obs_buffer));

    // Initial WiFi connect for NTP
    WiFi.mode(WIFI_STA);
    if (wifi_connect()) {
        if (sync_ntp()) {
            time_synced = true;
            last_flush_ts = time(nullptr);
        }
    } else {
        Serial.println("[WARN] No WiFi at boot — will retry on first flush");
    }

    // Switch to promiscuous mode
    wifi_disconnect_and_resume();
    esp_wifi_set_promiscuous_rx_cb(pkt_callback);

    current_channel = CHANNELS_24[0];
    esp_wifi_set_channel(current_channel, WIFI_SECOND_CHAN_NONE);

    Serial.printf("[SCAN] Started on channel %d\n", current_channel);

#if BLE_ENABLED
    // Start BLE last — it shares the radio controller with WiFi promisc and the
    // NimBLE stack interleaves scan windows automatically.
    memset(ble_live, 0, sizeof(ble_live));
    memset(ble_obs,  0, sizeof(ble_obs));
    setup_ble();
    Serial.printf("[HEAP] free=%u after BLE init\n", ESP.getFreeHeap());
#endif
}

// ── Loop ──────────────────────────────────────────────────────────────────────

void loop() {
    time_t now = time(nullptr);

    // Snapshot at each 10-second UTC boundary
    time_t slot_ts = (now / SLOT_SECONDS) * SLOT_SECONDS;
    if (slot_ts != last_slot_ts && now > 1000000000L) {
        take_snapshot(slot_ts);
#if BLE_ENABLED
        take_ble_snapshot(slot_ts);
#endif
        last_slot_ts = slot_ts;

        // Hop channel at each slot boundary (integer math avoids float precision loss)
        hop_channel(slot_ts);

#if BLE_ENABLED
        Serial.printf("[%lld] ch%-3d | wifi live:%-3d buf:%-3d | ble live:%-3d buf:%-3d\n",
                      (long long)slot_ts, current_channel,
                      live_count, obs_count, ble_live_count, ble_obs_count);
#else
        Serial.printf("[%lld] ch%-3d | live:%-3d buf:%-3d\n",
                      (long long)slot_ts, current_channel, live_count, obs_count);
#endif
    }

    // Flush to API every FLUSH_SECONDS
    bool have_data = obs_count > 0;
#if BLE_ENABLED
    have_data = have_data || ble_obs_count > 0;
#endif
    if (now - last_flush_ts >= FLUSH_SECONDS && have_data) {
        last_flush_ts = now;
        flush_to_api();
        return;
    }

    delay(50);
}
