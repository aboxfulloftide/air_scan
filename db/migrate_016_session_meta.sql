-- Session metadata: names, route fingerprints, route grouping
CREATE TABLE IF NOT EXISTS session_meta (
    session_id   VARCHAR(64) PRIMARY KEY,
    scanner_host VARCHAR(64),
    auto_name    VARCHAR(255),
    custom_name  VARCHAR(255),
    route_group  VARCHAR(64),       -- shared ID for sessions covering similar routes
    route_cells  JSON,              -- set of grid cells traversed (for similarity matching)
    start_lat    DECIMAL(10,7),
    start_lon    DECIMAL(10,7),
    end_lat      DECIMAL(10,7),
    end_lon      DECIMAL(10,7),
    start_address VARCHAR(255),
    end_address   VARCHAR(255),
    obs_count    INT DEFAULT 0,
    device_count INT DEFAULT 0,
    analyzed_at  DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_session_meta_route_group ON session_meta (route_group);
CREATE INDEX idx_session_meta_analyzed ON session_meta (analyzed_at);
