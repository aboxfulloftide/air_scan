-- Migration 013: Calibration walkthrough tables
-- Stores RSSI measurements taken at known physical locations for path-loss model tuning.

CREATE TABLE IF NOT EXISTS calibration_points (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    mac         VARCHAR(17) NOT NULL,
    lat         DECIMAL(12,8) NOT NULL,
    lon         DECIMAL(12,8) NOT NULL,
    floor       TINYINT DEFAULT 0,
    label       VARCHAR(128) DEFAULT NULL,
    captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cal_mac (mac),
    INDEX idx_cal_captured (captured_at)
);

CREATE TABLE IF NOT EXISTS calibration_readings (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    point_id        INT NOT NULL,
    scanner_host    VARCHAR(64) NOT NULL,
    avg_rssi        DECIMAL(6,2) NOT NULL,
    sample_count    INT NOT NULL DEFAULT 0,
    FOREIGN KEY (point_id) REFERENCES calibration_points(id) ON DELETE CASCADE,
    INDEX idx_calr_point (point_id)
);
