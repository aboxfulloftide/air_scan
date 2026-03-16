-- Migration 005: Triangulation indexes + settings
-- Safe to run multiple times (IF NOT EXISTS / INSERT IGNORE).

USE wireless;

-- Covering index for the triangulation query
CREATE INDEX idx_observations_recorded_signal
    ON observations (recorded_at, signal_dbm, mac, scanner_host);

-- Fast latest-per-mac lookups
CREATE INDEX idx_device_positions_mac_computed
    ON device_positions (mac, computed_at);

-- Seed triangulation settings
INSERT IGNORE INTO settings (key_name, value) VALUES
    ('triangulation_tx_power', '-40'),
    ('triangulation_path_loss_n', '2.7'),
    ('triangulation_interval_seconds', '30'),
    ('triangulation_window_seconds', '30'),
    ('position_retention_days', '1');
