# WiFi Device Scanner & Triangulation System

A distributed WiFi passive scanning system built for Raspberry Pi. Multiple scanners capture nearby device signals at synchronized clock boundaries and write to a shared MySQL database, enabling multi-point signal comparison and eventual device triangulation.

---

## Hardware

| Device | Role |
|--------|------|
| Raspberry Pi 5 (`office-pi5`) | Primary scanner |
| NetGear USB WiFi Adapter (`0846:9072`) | Monitor mode adapter (`wlan1`) |
| Remote MySQL server (`192.168.1.42`) | Central data store |

> The built-in Pi WiFi (`wlan0`) does not support monitor mode and is left in managed mode for network connectivity.

---

## How It Works

1. `wlan1` is placed into **monitor mode** at boot via a systemd service
2. The scanner passively sniffs **beacon frames** (access points) and **probe requests** (client devices)
3. Every **10 seconds**, at aligned UTC clock boundaries (`:00`, `:10`, `:20`...), the most recent signal reading and radio data per device is snapshotted
4. Every **60 seconds**, those snapshots (~6 per device) are batch-written to MySQL
5. Each observation is tagged with the scanner's **hostname** and **interface**, so multiple scanners writing to the same DB remain distinguishable

### Why aligned timestamps?
All scanners snap at the same UTC second. This allows direct signal comparison across scanners at any given moment — a prerequisite for triangulation.

### What is captured per packet
From the **RadioTap header** (every frame):
- Signal strength (`dBm`)
- Channel frequency (`MHz`) — precise 2.4 vs 5GHz
- Channel flags — modulation type (CCK, OFDM) and band

From **beacon frames** (access points):
- SSID, channel, frequency
- Encryption status and type (WPA2/WPA3) via RSN IE
- WiFi generation via capability IEs: 802.11n (HT), 802.11ac (VHT), 802.11ax/WiFi 6 (HE)
- Vendor-specific IE OUIs (identifies manufacturer tags, WPS, etc.)

From **probe requests** (client devices):
- SSIDs the device is actively searching for
- Capability IEs if present

From the **MAC address** (every device):
- OUI (first 3 bytes) → manufacturer name via scapy's embedded OUI database
- Locally administered bit → flags likely-randomized MACs

---

## Database Schema

**Database:** `wireless` on `192.168.1.42`

```
devices       -- one row per MAC address seen
observations  -- timestamped signal snapshot per device per scanner
ssids         -- SSIDs associated with each MAC
vendor_ies    -- vendor-specific IE OUIs seen per device
```

### `devices`
| Column | Type | Description |
|--------|------|-------------|
| `mac` | VARCHAR(17) | MAC address (primary key) |
| `device_type` | ENUM('AP','Client') | Access point or client device |
| `oui` | CHAR(8) | First 3 octets of MAC (`F8:79:0A`) |
| `manufacturer` | VARCHAR(64) | Resolved manufacturer name |
| `is_randomized` | TINYINT(1) | `1` if MAC locally administered bit is set |
| `ht_capable` | TINYINT(1) | Supports 802.11n |
| `vht_capable` | TINYINT(1) | Supports 802.11ac |
| `he_capable` | TINYINT(1) | Supports 802.11ax / WiFi 6 |
| `first_seen` | DATETIME | First time seen (UTC) |
| `last_seen` | DATETIME | Most recently seen (UTC) |

> Capability columns only update upward — once a device is seen as capable it stays that way, even if later packets omit those IEs.

### `observations`
| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGINT | Auto-increment primary key |
| `mac` | VARCHAR(17) | MAC address (FK → devices) |
| `interface` | VARCHAR(20) | Network interface (`wlan1`) |
| `scanner_host` | VARCHAR(64) | Hostname of the scanning Pi |
| `signal_dbm` | TINYINT | Signal strength in dBm at snapshot time |
| `channel` | TINYINT UNSIGNED | WiFi channel number |
| `freq_mhz` | SMALLINT UNSIGNED | Exact frequency in MHz (e.g. `2412`, `5180`) |
| `channel_flags` | VARCHAR(40) | Band and modulation (e.g. `2GHz+CCK`, `5GHz+OFDM`) |
| `recorded_at` | DATETIME | UTC timestamp of snapshot boundary |

### `ssids`
| Column | Type | Description |
|--------|------|-------------|
| `mac` | VARCHAR(17) | MAC address (FK → devices) |
| `ssid` | VARCHAR(255) | SSID seen associated with this MAC |
| `first_seen` | DATETIME | When this SSID was first observed (UTC) |

### `vendor_ies`
| Column | Type | Description |
|--------|------|-------------|
| `mac` | VARCHAR(17) | MAC address (FK → devices) |
| `vendor_oui` | VARCHAR(8) | OUI from vendor-specific IE (e.g. `00:50:f2` = Microsoft) |
| `first_seen` | DATETIME | When this vendor IE was first observed (UTC) |

---

## Files

| File | Description |
|------|-------------|
| `wifi_scanner.py` | Main scanner script |
| `setup_db.sql` | MySQL schema — run once on the database server |
| `README.md` | This file |

---

## Setup

### 1. Install dependencies

```bash
sudo apt-get install -y aircrack-ng tshark python3-scapy
sudo pip3 install mysql-connector-python --break-system-packages
```

### 2. Set up the database

Run once on the MySQL server (or from the Pi):

```bash
mysql -h 192.168.1.42 -u networkscan -p --skip-ssl wireless < setup_db.sql
```

> Note: `--skip-ssl` is required if the MySQL server uses a self-signed certificate.

### 3. Enable monitor mode at boot

Two systemd services handle this automatically:

```bash
# Monitor mode for wlan1
sudo cp wlan1-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable wlan1-monitor.service

# Scanner (depends on monitor mode service)
sudo systemctl enable wifi-scanner.service
```

Both services start automatically on boot in the correct order.

### 4. Run manually

```bash
sudo python3 ~/wifi_scanner.py
# or specify a different interface:
sudo python3 ~/wifi_scanner.py wlan2
```

---

## Systemd Services

| Service | Description |
|---------|-------------|
| `wlan1-monitor.service` | Puts `wlan1` into monitor mode at boot |
| `wifi-scanner.service` | Runs the scanner, depends on monitor mode service |

```bash
systemctl status wlan1-monitor.service
systemctl status wifi-scanner.service
sudo journalctl -fu wifi-scanner.service   # follow live logs
```

---

## Useful Queries

**Most recently seen devices with manufacturer:**
```sql
SELECT mac, device_type, manufacturer, is_randomized, last_seen
FROM devices
ORDER BY last_seen DESC
LIMIT 20;
```

**All WiFi 6 capable APs:**
```sql
SELECT mac, manufacturer, first_seen
FROM devices
WHERE device_type = 'AP' AND he_capable = 1;
```

**Compare signal from two scanners at the same timestamp:**
```sql
SELECT mac, scanner_host, interface, signal_dbm, freq_mhz, channel_flags
FROM observations
WHERE recorded_at = '2026-03-05 20:30:10'
ORDER BY mac, scanner_host;
```

**Signal history for a specific device:**
```sql
SELECT recorded_at, scanner_host, signal_dbm, channel, freq_mhz, channel_flags
FROM observations
WHERE mac = 'aa:bb:cc:dd:ee:ff'
ORDER BY recorded_at DESC
LIMIT 60;
```

**All SSIDs a client device has probed for:**
```sql
SELECT ssid, first_seen FROM ssids WHERE mac = 'aa:bb:cc:dd:ee:ff';
```

**Vendor IEs seen for an AP (identifies WPS, WPA, manufacturer tags):**
```sql
SELECT vendor_oui, first_seen FROM vendor_ies WHERE mac = 'aa:bb:cc:dd:ee:ff';
```

**Randomized MAC devices (unreliable for tracking):**
```sql
SELECT mac, manufacturer, first_seen FROM devices WHERE is_randomized = 1;
```

---

## Adding a Second Scanner

1. Set up the same dependencies and systemd services on the second Pi
2. Copy `wifi_scanner.py` — no changes needed, hostname is detected automatically via `socket.gethostname()`
3. Both scanners write to the same DB; `scanner_host` and `interface` together uniquely identify each source

---

## Storage Estimate

Observations drive almost all storage (~200 bytes/row including index overhead).

| Active Devices | Rows/Day | Storage/Day | Storage/Month |
|---------------|----------|-------------|---------------|
| 10 | 86,400 | ~17 MB | ~510 MB |
| 25 | 216,000 | ~43 MB | ~1.3 GB |
| 50 | 432,000 | ~86 MB | ~2.6 GB |
| 100 | 864,000 | ~172 MB | ~5.2 GB |

Consider a retention policy for `observations` (e.g. 90 days) while keeping `devices`, `ssids`, and `vendor_ies` indefinitely.

---

## Planned: Triangulation

With 3+ scanners recording signal strength at the same aligned timestamps, relative device position can be estimated using RSSI-based triangulation. The `observations` table is structured to support this — query by `recorded_at` to get all scanner readings for a device at any given moment. The `freq_mhz` and `channel_flags` columns are important here since 2.4 GHz and 5 GHz signals have very different propagation characteristics and should not be mixed in the same model.

---

## Notes

- All timestamps are stored in **UTC**
- MAC addresses may be randomized on modern devices (iOS, Android, Windows) — `is_randomized = 1` flags these upfront
- The scanner runs as root (required for raw socket access via scapy)
- If the DB is unreachable, observations are held in memory and retried on the next flush cycle
