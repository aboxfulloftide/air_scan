-- Migration 012: Tracker type detection for BLE observations
-- Adds adv_service_data and tracker_type columns to mobile_observations.
-- tracker_type: Apple:FindMy, Google FMDN, Tile, Samsung SmartTag, etc.

USE wireless;

ALTER TABLE mobile_observations
    ADD COLUMN adv_service_data VARCHAR(1024) NULL
        COMMENT 'BLE service data payloads, format: <uuid>:<hex>[,...]',
    ADD COLUMN tracker_type     VARCHAR(32)   NULL
        COMMENT 'Classified tracker type: Apple:FindMy, Google FMDN, Tile, etc.';

-- Index for querying tracker sightings
CREATE INDEX idx_mobile_tracker ON mobile_observations (tracker_type);
