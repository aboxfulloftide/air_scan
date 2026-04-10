-- Migration 017: BLE support for stationary scanners
-- Adds BLE-specific columns to main observations table and updates device_type enum.
-- The mobile_observations table already has some of these from migrate_010_ble.sql;
-- this migration brings the stationary scanner observations table to parity.

USE wireless;

-- Allow BLE as a device type
ALTER TABLE devices
    MODIFY COLUMN device_type ENUM('AP', 'Client', 'BLE') NOT NULL;

-- BLE-specific observation columns (nullable — WiFi rows leave these NULL)
ALTER TABLE observations
    ADD COLUMN manufacturer_data VARCHAR(512) NULL
        COMMENT 'Hex-encoded BLE manufacturer data, format: XXXX:<hex>[,...]',
    ADD COLUMN adv_services      VARCHAR(512) NULL
        COMMENT 'Comma-separated BLE advertised service UUIDs',
    ADD COLUMN adv_service_data  VARCHAR(512) NULL
        COMMENT 'Comma-separated UUID:<hex> BLE service data',
    ADD COLUMN tx_power          TINYINT      NULL
        COMMENT 'BLE advertised TX power (dBm)',
    ADD COLUMN tracker_type      VARCHAR(32)  NULL
        COMMENT 'Tracker classification (Apple:FindMy, Google FMDN, Tile, etc.)';

-- Index for quick tracker lookups
CREATE INDEX idx_observations_tracker ON observations (tracker_type);
