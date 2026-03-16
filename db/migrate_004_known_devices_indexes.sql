-- Migration 004: indexes for known_devices search and host-based lookups
-- Safe to run multiple times.

DROP PROCEDURE IF EXISTS add_index_if_missing;
DELIMITER //
CREATE PROCEDURE add_index_if_missing(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN idx_col VARCHAR(128))
BEGIN
    SET @exists = (
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl AND INDEX_NAME = idx
    );
    IF @exists = 0 THEN
        SET @sql = CONCAT('CREATE INDEX `', idx, '` ON `', tbl, '` (', idx_col, ')');
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//
DELIMITER ;

CALL add_index_if_missing('known_devices', 'idx_known_devices_status', '`status`');
CALL add_index_if_missing('known_devices', 'idx_known_devices_port_scan_host_id', '`port_scan_host_id`');
CALL add_index_if_missing('known_devices', 'idx_known_devices_port_scan_host_id_synced_at', '`port_scan_host_id`, `synced_at`');
CALL add_index_if_missing('known_devices', 'idx_known_devices_label', '`label`');
CALL add_index_if_missing('known_devices', 'idx_known_devices_owner', '`owner`');
CALL add_index_if_missing('devices', 'idx_devices_device_type_last_seen', '`device_type`, `last_seen`');

DROP PROCEDURE IF EXISTS add_index_if_missing;
