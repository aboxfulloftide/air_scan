-- Migration 007: Add GPS columns to observations for mobile scanner
-- Fixed scanners will have NULL in these columns.
-- Mobile scanner populates them via mobile_sync.py on upload.

USE wireless;

ALTER TABLE observations
    ADD COLUMN gps_lat DECIMAL(10, 7) NULL AFTER channel_flags,
    ADD COLUMN gps_lon DECIMAL(10, 7) NULL AFTER gps_lat;

-- Index for map/query lookups on GPS-tagged observations
CREATE INDEX idx_observations_gps ON observations (gps_lat, gps_lon);
