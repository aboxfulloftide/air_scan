-- Migration 006: Per-scanner RSSI correction + temporal averaging
-- Safe to run multiple times.

USE wireless;

-- Store computed RSSI offset on each scanner for visibility
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

CALL add_column_if_missing('scanners', 'rssi_offset', 'DECIMAL(5,2) DEFAULT 0 COMMENT "Auto-computed RSSI correction (dB)"', 'z_pos');
CALL add_column_if_missing('scanners', 'calibration_samples', 'SMALLINT DEFAULT 0 COMMENT "Number of fixed devices used for calibration"', 'rssi_offset');

DROP PROCEDURE IF EXISTS add_column_if_missing;

-- Bump default RSSI window from 30s to 120s for temporal averaging
UPDATE settings SET value = '120'
    WHERE key_name = 'triangulation_window_seconds' AND value = '30';

-- RSSI correction enabled by default
INSERT IGNORE INTO settings (key_name, value) VALUES
    ('triangulation_rssi_correction', 'true');
