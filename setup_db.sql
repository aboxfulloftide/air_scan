-- Run this on the MySQL server at 192.168.1.42
-- mysql -u networkscan -p wireless < setup_db.sql

USE wireless;

CREATE TABLE IF NOT EXISTS devices (
    mac             VARCHAR(17) NOT NULL PRIMARY KEY,
    device_type     ENUM('AP', 'Client') NOT NULL,
    first_seen      DATETIME NOT NULL,
    last_seen       DATETIME NOT NULL,
    INDEX idx_last_seen (last_seen)
);

CREATE TABLE IF NOT EXISTS observations (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    mac             VARCHAR(17) NOT NULL,
    interface       VARCHAR(20) NOT NULL,
    signal_dbm      TINYINT,
    channel         TINYINT UNSIGNED,
    recorded_at     DATETIME NOT NULL,
    INDEX idx_mac (mac),
    INDEX idx_recorded_at (recorded_at),
    FOREIGN KEY (mac) REFERENCES devices(mac) ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS ssids (
    mac             VARCHAR(17) NOT NULL,
    ssid            VARCHAR(255) NOT NULL,
    first_seen      DATETIME NOT NULL,
    PRIMARY KEY (mac, ssid),
    FOREIGN KEY (mac) REFERENCES devices(mac) ON UPDATE CASCADE
);
