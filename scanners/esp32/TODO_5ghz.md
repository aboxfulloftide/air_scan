# ESP32-C5 5GHz Support

The ESP32-C5 supports dual-band WiFi 6 (2.4 + 5GHz) but firmware currently only scans 2.4GHz.

## What needs to change

**config.h**
- Add `CHANNELS_5[]` array with 5GHz channels (36,40,44,48,149,153,157,161,165)
- Add `NUM_CHANNELS_5`

**esp32_scanner.ino**
- `pick_channel()`: split the 60s cycle between bands (e.g. 30s on 2.4, 30s on 5) same way Pi scanner does with `build_schedule()`
- `channel_to_freq()`: already handles 5GHz math, just needs the right channel numbers fed in
- `esp_wifi_set_channel()`: pass `WIFI_SECOND_CHAN_NONE` — same call works for 5GHz
