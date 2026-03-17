-- Migration 009: Firmware release tracking for ESP32 OTA updates
-- Stores metadata for each firmware binary; is_current flags the active release.

USE wireless;

CREATE TABLE IF NOT EXISTS firmware_releases (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    version     VARCHAR(20)  NOT NULL,
    platform    VARCHAR(32)  NOT NULL DEFAULT 'esp32',
    filename    VARCHAR(255) NOT NULL,
    sha256      CHAR(64)     NOT NULL,
    size_bytes  INT UNSIGNED NOT NULL,
    notes       TEXT,
    is_current  TINYINT(1)   NOT NULL DEFAULT 0,
    uploaded_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_version_platform (version, platform),
    INDEX idx_firmware_platform_current (platform, is_current)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
