-- Migration 003: fixed-device anchors for map placement
-- Safe to run multiple times on older MySQL/MariaDB versions.

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

CALL add_column_if_missing('known_devices', 'is_fixed', 'BOOLEAN NOT NULL DEFAULT FALSE', 'synced_at');
CALL add_column_if_missing('known_devices', 'fixed_x', 'DECIMAL(12,8) NULL', 'is_fixed');
CALL add_column_if_missing('known_devices', 'fixed_y', 'DECIMAL(12,8) NULL', 'fixed_x');
CALL add_column_if_missing('known_devices', 'fixed_z', 'DECIMAL(12,8) NULL', 'fixed_y');
CALL add_column_if_missing('known_devices', 'fixed_floor', 'TINYINT DEFAULT 0', 'fixed_z');

ALTER TABLE device_positions
    MODIFY COLUMN method ENUM('trilateration', 'single_scanner', 'gps', 'manual', 'fixed');

DROP PROCEDURE IF EXISTS add_column_if_missing;
