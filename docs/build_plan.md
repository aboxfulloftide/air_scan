# Air Scan Build Plan

## Goal
Extend the WiFi scanner system to support device triangulation across 3-4 fixed scanners,
integrate with the port_scan project for known-device identification, support a mobile
scanner with GPS, and visualize everything on a property map.

---

## Current State

### air_scan
- Collects WiFi probe requests and beacon frames via scapy (monitor mode)
- Two scanner types: `wifi_scanner.py` (direct scapy on Pi5) and `pull_scanner.py` (OpenWrt pull)
- Stores MAC, signal_dbm, channel, freq, scanner_host, probe_count in MySQL (`wireless` DB)
- 10-second UTC-aligned snapshots for cross-scanner comparison
- Each snapshot includes `probe_count` — the number of raw packets seen for that device during the window
- Offline JSONL buffering when DB is unreachable

### port_scan
- Tracks network hosts by IP/MAC/hostname with FastAPI + SQLAlchemy
- MySQL DB (`port_scan`) with host merge/alias system
- Host identity via `hosts.current_mac` and `host_network_ids.mac_address`
- Full web UI (React + TailwindCSS)

### Join Point
**MAC address** — port_scan tracks `hosts.current_mac` and `host_network_ids.mac_address`,
air_scan tracks `devices.mac`. This correlates known network devices with wireless observations.

---

## Phase 1: Scanner Infrastructure (foundation)

### 1a. Schema Cleanup
The `setup_db.sql` is outdated vs what the code actually writes. Update to match reality:
- `devices`: add `oui`, `manufacturer`, `is_randomized`, `ht_capable`, `vht_capable`, `he_capable`
- `observations`: add `scanner_host`, `freq_mhz`, `channel_flags`
- `vendor_ies` table (missing from schema entirely)
- Write a migration script for existing databases

### 1b. Scanner Registry
New table `scanners`:
- `id`, `hostname`, `label`, `x_pos`, `y_pos`, `z_pos`, `floor`, `is_active`, `last_heartbeat`
- Scanners self-register on startup via hostname
- Links to existing `scanner_host` column in `observations`

### 1c. Property Map Config
New tables:
- `map_config` — property/floor plan metadata, image paths, GPS anchor points, dimensions
- `map_zones` — named zones (garage, office, yard) as polygon coordinates

---

## Phase 2: Triangulation Engine

### 2a. Signal Data (already collected)
Current `observations` table has `signal_dbm` + `scanner_host` per 10s slot.
For each time window, 3+ scanners hearing the same MAC = triangulation input.

### 2b. Triangulation Algorithm
New module `triangulation.py`:
- **RSSI to distance**: log-distance path loss model
  `d = 10^((TxPower - RSSI) / (10 * n))` where n = path-loss exponent (tunable)
- **Trilateration**: least-squares from 3+ distance estimates
- Configurable calibration via known-distance reference points

### 2c. Position Storage
New table `device_positions`:
- `mac`, `x`, `y`, `floor`, `confidence`, `scanner_count`, `method`, `computed_at`
- Methods: `trilateration`, `single_scanner`, `gps`, `fixed`
- Retains history for movement tracking

### 2d. Fixed-Position Devices (static anchors)
Devices like Rokus, smart TVs, and printers are always in the same physical spot.
These can be manually pinned on the map and serve dual purpose:

**Map placement**: Their dot always renders at the pinned location regardless of
RSSI-computed position.

**Triangulation calibration**: Since the true position is known, observed RSSI
readings from each scanner can be used to tune the path-loss exponent `n` per
scanner pair. A fixed device with strong consistent signal = a free calibration
beacon.

Schema additions to `known_devices`:
- `is_fixed` BOOLEAN — device has a manually set position
- `fixed_x`, `fixed_y` DECIMAL — map coordinates (same coordinate space as scanners)
- `fixed_floor` TINYINT — floor number

When `is_fixed = TRUE`, `device_positions` rows for that MAC use `method = 'fixed'`
and always write the pinned coordinates. The triangulation engine skips computing
a new position for fixed devices but can still use their observations as calibration
data.

---

## Phase 3: Mobile Scanner

### 3a. Upload API
FastAPI backend with endpoints:
- `POST /api/upload` — batch observations with GPS coordinates
- `POST /api/upload/pcap` — raw pcap + GPS metadata

### 3b. GPS-to-Local Coordinate Transform
- Define anchor points mapping GPS lat/lon to property map x,y
- Affine transform for conversion
- Mobile observations feed into same triangulation pipeline

### 3c. Mobile Scanner Script
Python script for laptop/phone with monitor-mode WiFi:
- Captures WiFi + GPS simultaneously
- Buffers locally, uploads when connected
- Extends `wifi_scanner.py` with GPS fields

---

## Phase 4: Port Scan Integration (known device marking)

### 4a. Cross-Database Linking
New table `known_devices` in `wireless` DB:
- `mac`, `port_scan_host_id`, `label`, `owner`, `status`
- Sync job periodically pulls MACs from port_scan `host_network_ids`

### 4b. Device Classification
Extend `devices` with `known_status` enum: `known`, `unknown`, `guest`, `rogue`
- Auto-classify: MAC in port_scan = `known`, otherwise = `unknown`
- Manual override via UI

### 4c. Alerts
- Unknown device in secure zone
- Known device in unexpected location
- New device not in port_scan DB

---

## Phase 5: Web UI / Visualization

### 5a. Property Map View
Interactive map with:
- Scanner positions (fixed icons)
- Device positions (dots colored by known/unknown/rogue)
- Signal strength heat map overlay
- Zone boundaries

### 5b. Device Detail View
Click a device to see: MAC, manufacturer, SSIDs probed, signal history,
port scan data (if known), position history trail

### 5c. Dashboard
- Device count by zone
- Known vs unknown breakdown
- Scanner health status
- Recent alerts

---

## Future: SDR Integration

Fixed and mobile scanners may include SDR (Software Defined Radio) hardware alongside WiFi.
SDR could capture Bluetooth, Zigbee, Z-Wave, cellular, and other RF signals. Data format,
storage schema, and integration approach TBD — depends on what SDR tooling and protocols
are targeted. Placeholder for when requirements become clearer.

---

## Build Order

| Priority | Phase | What | Why |
|----------|-------|------|-----|
| 1 | 1 | Scanner registry + schema cleanup | Foundation for everything spatial |
| 2 | 4a | Port scan MAC linking | Quick win — known vs unknown |
| 3 | 4b | Device classification UI | Needed to pin fixed-position devices |
| 4 | 2d | Fixed-position device anchors | Pin Rokus/TVs/printers on map; calibration data for triangulation |
| 5 | 3a | API for mobile upload | Enables GPS data collection |
| 6 | 2a-c | Triangulation engine | Needs scanner positions + fixed anchors for calibration |
| 7 | 5 | Web UI map view | Visual payoff |

---

## Schema Changes Summary

```sql
-- Phase 1: Scanner registry
CREATE TABLE scanners (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    hostname        VARCHAR(64) UNIQUE NOT NULL,
    label           VARCHAR(128),
    x_pos           DECIMAL(10,4),
    y_pos           DECIMAL(10,4),
    z_pos           DECIMAL(10,4) DEFAULT 0,
    floor           TINYINT DEFAULT 0,
    is_active       BOOLEAN DEFAULT TRUE,
    last_heartbeat  DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Phase 1: Property map
CREATE TABLE map_config (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    label           VARCHAR(128) NOT NULL,
    floor           TINYINT DEFAULT 0,
    image_path      VARCHAR(512),
    width_meters    DECIMAL(10,2),
    height_meters   DECIMAL(10,2),
    gps_anchor_lat  DECIMAL(12,8),
    gps_anchor_lon  DECIMAL(12,8),
    gps_anchor_x    DECIMAL(10,4),
    gps_anchor_y    DECIMAL(10,4)
);

CREATE TABLE map_zones (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    map_id          INT NOT NULL,
    label           VARCHAR(128) NOT NULL,
    polygon_json    JSON NOT NULL,
    zone_type       ENUM('secure','common','outdoor') DEFAULT 'common',
    FOREIGN KEY (map_id) REFERENCES map_config(id) ON DELETE CASCADE
);

-- Phase 2: Computed positions
CREATE TABLE device_positions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    mac             VARCHAR(17) NOT NULL,
    x_pos           DECIMAL(10,4),
    y_pos           DECIMAL(10,4),
    floor           TINYINT,
    confidence      DECIMAL(5,2),
    method          ENUM('trilateration','single_scanner','gps','fixed'),
    scanner_count   TINYINT,
    computed_at     DATETIME NOT NULL,
    INDEX idx_mac (mac),
    INDEX idx_computed_at (computed_at)
);

-- Phase 4: Known device cross-reference
CREATE TABLE known_devices (
    mac             VARCHAR(17) PRIMARY KEY,
    port_scan_host_id INT,
    label           VARCHAR(128),
    owner           VARCHAR(128),
    status          ENUM('known','unknown','guest','rogue') DEFAULT 'unknown',
    synced_at       DATETIME,
    -- Phase 2d: fixed-position anchor support
    is_fixed        BOOLEAN DEFAULT FALSE,
    fixed_x         DECIMAL(10,4),
    fixed_y         DECIMAL(10,4),
    fixed_floor     TINYINT DEFAULT 0
);
```
