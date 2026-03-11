-- Migration 001: Phase 1 — Schema cleanup + scanner infrastructure
-- Run on existing database: mysql -u networkscan -p wireless < migrate_001_phase1.sql
--
-- Safe to run multiple times — checks for existing columns/tables/indexes.

USE wireless;

-- -----------------------------------------------------------------------
-- Helper: add column only if it doesn't exist
-- -----------------------------------------------------------------------

DROP PROCEDURE IF EXISTS add_column_if_missing;
DELIMITER //
CREATE PROCEDURE add_column_if_missing(
    IN tbl VARCHAR(64), IN col VARCHAR(64), IN col_def TEXT, IN after_col VARCHAR(64)
)
BEGIN
    SET @exists = (
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl AND COLUMN_NAME = col
    );
    IF @exists = 0 THEN
        IF after_col IS NOT NULL THEN
            SET @sql = CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', col_def, ' AFTER `', after_col, '`');
        ELSE
            SET @sql = CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', col_def);
        END IF;
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//
DELIMITER ;

-- -----------------------------------------------------------------------
-- 1. devices table — add columns the code already writes
-- -----------------------------------------------------------------------

CALL add_column_if_missing('devices', 'oui',           'CHAR(8)',                       'device_type');
CALL add_column_if_missing('devices', 'manufacturer',  'VARCHAR(64)',                   'oui');
CALL add_column_if_missing('devices', 'is_randomized', 'TINYINT(1) NOT NULL DEFAULT 0', 'manufacturer');
CALL add_column_if_missing('devices', 'ht_capable',    'TINYINT(1) NOT NULL DEFAULT 0', 'is_randomized');
CALL add_column_if_missing('devices', 'vht_capable',   'TINYINT(1) NOT NULL DEFAULT 0', 'ht_capable');
CALL add_column_if_missing('devices', 'he_capable',    'TINYINT(1) NOT NULL DEFAULT 0', 'vht_capable');

-- -----------------------------------------------------------------------
-- 2. observations table — add columns the code already writes
-- -----------------------------------------------------------------------

CALL add_column_if_missing('observations', 'scanner_host',  "VARCHAR(64) NOT NULL DEFAULT ''", 'interface');
CALL add_column_if_missing('observations', 'freq_mhz',      'SMALLINT UNSIGNED',               'channel');
CALL add_column_if_missing('observations', 'channel_flags',  'VARCHAR(40)',                     'freq_mhz');

-- Index for scanner-based queries (triangulation needs this)
DROP PROCEDURE IF EXISTS add_index_if_missing;
DELIMITER //
CREATE PROCEDURE add_index_if_missing(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN idx_col VARCHAR(64))
BEGIN
    SET @exists = (
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl AND INDEX_NAME = idx
    );
    IF @exists = 0 THEN
        SET @sql = CONCAT('CREATE INDEX `', idx, '` ON `', tbl, '` (`', idx_col, '`)');
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//
DELIMITER ;

CALL add_index_if_missing('observations', 'idx_scanner_host', 'scanner_host');

-- -----------------------------------------------------------------------
-- 3. vendor_ies table — missing from original schema
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vendor_ies (
    mac             VARCHAR(17) NOT NULL,
    vendor_oui      VARCHAR(8) NOT NULL,
    first_seen      DATETIME NOT NULL,
    PRIMARY KEY (mac, vendor_oui),
    FOREIGN KEY (mac) REFERENCES devices(mac) ON UPDATE CASCADE
);

-- -----------------------------------------------------------------------
-- 4. Scanner registry
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS scanners (
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

-- -----------------------------------------------------------------------
-- 5. Property map tables
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
-- 6. Device positions (Phase 2, create now)
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS device_positions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    mac             VARCHAR(17) NOT NULL,
    x_pos           DECIMAL(10,4),
    y_pos           DECIMAL(10,4),
    floor           TINYINT,
    confidence      DECIMAL(5,2),
    method          ENUM('trilateration', 'single_scanner', 'gps'),
    scanner_count   TINYINT,
    computed_at     DATETIME NOT NULL,
    INDEX idx_mac (mac),
    INDEX idx_computed_at (computed_at)
);

-- -----------------------------------------------------------------------
-- 7. Known devices cross-reference (Phase 4, create now)
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
-- Cleanup helper procedures
-- -----------------------------------------------------------------------

DROP PROCEDURE IF EXISTS add_column_if_missing;
DROP PROCEDURE IF EXISTS add_index_if_missing;
