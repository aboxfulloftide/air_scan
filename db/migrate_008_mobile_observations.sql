-- Migration 008: Dedicated table for mobile scanner observations
-- Keeps wardriving/survey data separate from fixed scanner observations.
-- GPS columns are first-class here (not nullable afterthoughts).
-- Shares the devices table for MAC metadata.

USE wireless;

CREATE TABLE IF NOT EXISTS mobile_observations (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    mac             VARCHAR(17) NOT NULL,
    interface       VARCHAR(20) NOT NULL,
    scanner_host    VARCHAR(64) NOT NULL,
    signal_dbm      TINYINT,
    channel         TINYINT UNSIGNED,
    freq_mhz        SMALLINT UNSIGNED,
    channel_flags   VARCHAR(40),
    gps_lat         DECIMAL(10,7),
    gps_lon         DECIMAL(10,7),
    gps_fix         TINYINT(1) NOT NULL DEFAULT 0,
    session_id      VARCHAR(64)  COMMENT 'Scanner hostname + session start timestamp',
    recorded_at     DATETIME NOT NULL,
    INDEX idx_mobile_mac         (mac),
    INDEX idx_mobile_recorded_at (recorded_at),
    INDEX idx_mobile_scanner     (scanner_host),
    INDEX idx_mobile_gps         (gps_lat, gps_lon),
    INDEX idx_mobile_session     (session_id),
    FOREIGN KEY (mac) REFERENCES devices(mac) ON UPDATE CASCADE
);
