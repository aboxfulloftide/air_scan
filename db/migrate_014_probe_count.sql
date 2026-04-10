-- Add probe_count to observations: how many raw probes/beacons the scanner
-- saw for this MAC during the 10-second window.  Existing rows get 1.
ALTER TABLE observations
    ADD COLUMN probe_count SMALLINT UNSIGNED NOT NULL DEFAULT 1
    AFTER channel_flags;
