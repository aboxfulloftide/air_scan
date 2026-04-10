-- Partition observations table by day for instant cleanup.
--
-- MySQL constraints:
--   1. Partition key must be in every unique index → add recorded_at to PK
--   2. Foreign keys not allowed on partitioned tables → drop FK
--
-- This migration:
--   1. Creates a new partitioned table
--   2. Copies existing data (only last 3 days to start clean)
--   3. Swaps the tables
--   4. Creates partitions for the next 7 days
--
-- Run during a low-traffic window — the data copy may take a minute.

-- Step 1: Create the new partitioned table
CREATE TABLE observations_new (
    id              BIGINT UNSIGNED AUTO_INCREMENT,
    mac             VARCHAR(17) NOT NULL,
    interface       VARCHAR(20) NOT NULL,
    scanner_host    VARCHAR(64) NOT NULL DEFAULT '',
    signal_dbm      TINYINT,
    channel         TINYINT UNSIGNED,
    freq_mhz        SMALLINT UNSIGNED,
    channel_flags   VARCHAR(40),
    probe_count     SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    gps_lat         DECIMAL(10,7),
    gps_lon         DECIMAL(10,7),
    recorded_at     DATETIME NOT NULL,
    PRIMARY KEY (id, recorded_at),
    KEY idx_mac (mac),
    KEY idx_recorded_at (recorded_at),
    KEY idx_scanner_host (scanner_host),
    KEY idx_observations_recorded_signal (recorded_at, signal_dbm, mac, scanner_host),
    KEY idx_observations_gps (gps_lat, gps_lon),
    KEY interface (interface)
) ENGINE=InnoDB
PARTITION BY RANGE (TO_DAYS(recorded_at)) (
    PARTITION p20260401 VALUES LESS THAN (TO_DAYS('2026-04-02')),
    PARTITION p20260402 VALUES LESS THAN (TO_DAYS('2026-04-03')),
    PARTITION p20260403 VALUES LESS THAN (TO_DAYS('2026-04-04')),
    PARTITION p20260404 VALUES LESS THAN (TO_DAYS('2026-04-05')),
    PARTITION p20260405 VALUES LESS THAN (TO_DAYS('2026-04-06')),
    PARTITION p20260406 VALUES LESS THAN (TO_DAYS('2026-04-07')),
    PARTITION p20260407 VALUES LESS THAN (TO_DAYS('2026-04-08')),
    PARTITION p20260408 VALUES LESS THAN (TO_DAYS('2026-04-09')),
    PARTITION p20260409 VALUES LESS THAN (TO_DAYS('2026-04-10')),
    PARTITION p20260410 VALUES LESS THAN (TO_DAYS('2026-04-11')),
    PARTITION p_future  VALUES LESS THAN MAXVALUE
);

-- Step 2: Copy only last 3 days of data (skip the 10M+ old rows)
INSERT INTO observations_new
    (mac, interface, scanner_host, signal_dbm, channel, freq_mhz,
     channel_flags, probe_count, gps_lat, gps_lon, recorded_at)
SELECT mac, interface, scanner_host, signal_dbm, channel, freq_mhz,
       channel_flags, probe_count, gps_lat, gps_lon, recorded_at
FROM observations
WHERE recorded_at >= UTC_TIMESTAMP() - INTERVAL 3 DAY;

-- Step 3: Swap tables
RENAME TABLE observations TO observations_old,
             observations_new TO observations;

-- Step 4: Drop the old table (frees disk space)
DROP TABLE observations_old;
