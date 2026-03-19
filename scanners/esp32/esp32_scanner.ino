/*
 * Air Scan — ESP32 Static Scanner
 *
 * Passively captures 802.11 beacon and probe-request frames on 2.4 GHz.
 * Tracks best RSSI per MAC per 10-second window (same slot boundaries as
 * the Pi scanners). Every FLUSH_SECONDS, reconnects to WiFi and POSTs the
 * buffered observations to the API.
 *
 * Required libraries (install via Arduino Library Manager):
 *   - ArduinoJson  (Benoit Blanchon, v6.x)
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
 * ─────────────────────────────────────────────────────────────────────────────
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include <ArduinoJson.h>
#include <esp_wifi.h>
#include <time.h>
#include "config.h"

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
        strncpy(live[idx].ssid, ssid, 32);
    } else {
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

// ── Channel hopping ────────────────────────────────────────────────────────────

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
            // Radio needs promiscuous restart when crossing bands on ESP32-C5
            esp_wifi_set_promiscuous(false);
            esp_wifi_set_band_mode(WIFI_BAND_MODE_AUTO);
            esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
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
    if (obs_count == 0) return;

    Serial.printf("[FLUSH] %d observations — connecting WiFi\n", obs_count);

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
    // Each observation: ~120 bytes JSON; 300 obs = ~36 KB
    DynamicJsonDocument doc(40960);
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
        obj["interface"]   = SCAN_IFACE;
        obj["recorded_at"] = ts_str;
    }

    String payload;
    serializeJson(doc, payload);

    unsigned long t_json = millis();

    HTTPClient http;
    String url = String(API_HOST) + API_UPLOAD_PATH;
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(10000);

    int code = http.POST(payload);

    unsigned long t_post = millis();
    Serial.printf("[BENCH] wifi+ntp=%lums  json=%lums  post=%lums  total=%lums\n",
                  t_wifi - t0, t_json - t_wifi, t_post - t_json, t_post - t0);

    if (code > 0) {
        Serial.printf("[API] POST %d — %s\n", code, http.getString().c_str());
        // Clear buffer only on success
        portENTER_CRITICAL(&buf_mux);
        obs_count = 0;
        portEXIT_CRITICAL(&buf_mux);

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
}

// ── Loop ──────────────────────────────────────────────────────────────────────

void loop() {
    time_t now = time(nullptr);

    // Snapshot at each 10-second UTC boundary
    time_t slot_ts = (now / SLOT_SECONDS) * SLOT_SECONDS;
    if (slot_ts != last_slot_ts && now > 1000000000L) {
        take_snapshot(slot_ts);
        last_slot_ts = slot_ts;

        // Hop channel at each slot boundary (integer math avoids float precision loss)
        hop_channel(slot_ts);

        Serial.printf("[%lld] ch%-3d | live:%-3d buf:%-3d\n",
                      (long long)slot_ts, current_channel, live_count, obs_count);
    }

    // Flush to API every FLUSH_SECONDS
    if (now - last_flush_ts >= FLUSH_SECONDS && obs_count > 0) {
        last_flush_ts = now;
        flush_to_api();
        return;
    }

    delay(50);
}
