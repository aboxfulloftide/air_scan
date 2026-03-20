-- Migration 010: BLE scanner support
-- Adds 'BLE' to the devices.device_type enum and BLE-specific columns
-- to mobile_observations so wardriving BLE data can be synced from the Pi.

USE wireless;

-- Allow BLE as a device type
ALTER TABLE devices
    MODIFY COLUMN device_type ENUM('AP', 'Client', 'BLE') NOT NULL;

-- BLE-specific observation columns (nullable — WiFi rows leave these NULL)
ALTER TABLE mobile_observations
    ADD COLUMN manufacturer_data VARCHAR(512) NULL
        COMMENT 'Hex-encoded BLE manufacturer data, format: XXXX:<hex>[,...]',
    ADD COLUMN adv_services      VARCHAR(512) NULL
        COMMENT 'Comma-separated BLE advertised service UUIDs',
    ADD COLUMN tx_power          TINYINT      NULL
        COMMENT 'BLE advertised TX power (dBm)';
