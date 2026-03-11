-- Air Scan database schema
-- Run on MySQL server: mysql -u networkscan -p wireless < setup_db.sql

USE wireless;

-- -----------------------------------------------------------------------
-- Core tables
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS devices (
    mac             VARCHAR(17) NOT NULL PRIMARY KEY,
    device_type     ENUM('AP', 'Client') NOT NULL,
    oui             CHAR(8),
    manufacturer    VARCHAR(64),
    is_randomized   TINYINT(1) NOT NULL DEFAULT 0,
    ht_capable      TINYINT(1) NOT NULL DEFAULT 0,
    vht_capable     TINYINT(1) NOT NULL DEFAULT 0,
    he_capable      TINYINT(1) NOT NULL DEFAULT 0,
    first_seen      DATETIME NOT NULL,
    last_seen       DATETIME NOT NULL,
    INDEX idx_last_seen (last_seen)
);

CREATE TABLE IF NOT EXISTS observations (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    mac             VARCHAR(17) NOT NULL,
    interface       VARCHAR(20) NOT NULL,
    scanner_host    VARCHAR(64) NOT NULL,
    signal_dbm      TINYINT,
    channel         TINYINT UNSIGNED,
    freq_mhz        SMALLINT UNSIGNED,
    channel_flags   VARCHAR(40),
    recorded_at     DATETIME NOT NULL,
    INDEX idx_mac (mac),
    INDEX idx_recorded_at (recorded_at),
    INDEX idx_scanner_host (scanner_host),
    FOREIGN KEY (mac) REFERENCES devices(mac) ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS ssids (
    mac             VARCHAR(17) NOT NULL,
    ssid            VARCHAR(255) NOT NULL,
    first_seen      DATETIME NOT NULL,
    PRIMARY KEY (mac, ssid),
    FOREIGN KEY (mac) REFERENCES devices(mac) ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS vendor_ies (
    mac             VARCHAR(17) NOT NULL,
    vendor_oui      VARCHAR(8) NOT NULL,
    first_seen      DATETIME NOT NULL,
    PRIMARY KEY (mac, vendor_oui),
    FOREIGN KEY (mac) REFERENCES devices(mac) ON UPDATE CASCADE
);

-- -----------------------------------------------------------------------
-- Scanner infrastructure (Phase 1)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS scanners (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    hostname        VARCHAR(64) UNIQUE NOT NULL,
    label           VARCHAR(128),
    x_pos           DECIMAL(12,8),
    y_pos           DECIMAL(12,8),
    z_pos           DECIMAL(12,8) DEFAULT 0,
    floor           TINYINT DEFAULT 0,
    is_active       BOOLEAN DEFAULT TRUE,
    last_heartbeat  DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------
-- Property map (Phase 1)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS map_config (
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

CREATE TABLE IF NOT EXISTS map_zones (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    map_id          INT NOT NULL,
    label           VARCHAR(128) NOT NULL,
    polygon_json    JSON NOT NULL,
    zone_type       ENUM('secure', 'common', 'outdoor') DEFAULT 'common',
    FOREIGN KEY (map_id) REFERENCES map_config(id) ON DELETE CASCADE
);

-- -----------------------------------------------------------------------
-- Computed positions (Phase 2 — created now for forward compatibility)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS device_positions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    mac             VARCHAR(17) NOT NULL,
    x_pos           DECIMAL(12,8),
    y_pos           DECIMAL(12,8),
    z_pos           DECIMAL(12,8),
    floor           TINYINT,
    confidence      DECIMAL(5,2),
    method          ENUM('trilateration', 'single_scanner', 'gps', 'manual'),
    scanner_count   TINYINT,
    computed_at     DATETIME NOT NULL,
    INDEX idx_mac (mac),
    INDEX idx_computed_at (computed_at)
);

-- -----------------------------------------------------------------------
-- Known device cross-reference (Phase 4 — created now for forward compat)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS known_devices (
    mac             VARCHAR(17) PRIMARY KEY,
    port_scan_host_id INT,
    label           VARCHAR(128),
    owner           VARCHAR(128),
    status          ENUM('known', 'unknown', 'guest', 'rogue') DEFAULT 'unknown',
    synced_at       DATETIME
);


-- -----------------------------------------------------------------------
-- Settings (key-value config store)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS settings (
  key_name   VARCHAR(64) PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT IGNORE INTO settings (key_name, value) VALUES
  ('observation_retention_days', '3');
