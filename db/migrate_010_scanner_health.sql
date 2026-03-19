-- migrate_010_scanner_health.sql
-- Per-flush device health snapshots from ESP32 scanners

CREATE TABLE scanner_health (
    id            BIGINT UNSIGNED  AUTO_INCREMENT PRIMARY KEY,
    scanner_host  VARCHAR(64)      NOT NULL,
    mac           VARCHAR(17)      NULL,
    free_heap     INT UNSIGNED     NULL,   -- bytes currently free
    min_free_heap INT UNSIGNED     NULL,   -- lowest free heap since boot
    uptime_ms     BIGINT UNSIGNED  NULL,   -- millis() at flush time
    temperature_c DECIMAL(5,2)    NULL,   -- internal sensor (~10°C hot)
    recorded_at   DATETIME         NOT NULL,
    INDEX idx_health_host        (scanner_host),
    INDEX idx_health_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
