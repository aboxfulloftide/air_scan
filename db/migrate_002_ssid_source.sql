-- Migration 002: Add source column to ssids table
-- Tracks how an SSID was learned: beacon, probe_response, or association.
-- Run: mysql -h 192.168.1.42 -u networkscan -p wireless < db/migrate_002_ssid_source.sql

USE wireless;

-- Check: SHOW COLUMNS FROM ssids LIKE 'source'; — skip if already present
ALTER TABLE ssids
    ADD COLUMN source
        ENUM('beacon','probe_response','association') NOT NULL DEFAULT 'beacon'
    AFTER first_seen;
