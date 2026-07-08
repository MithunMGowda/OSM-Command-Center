-- OSM operations warehouse (PostgreSQL + PostGIS optional)
CREATE TABLE IF NOT EXISTS osm_changesets (
    changeset_id BIGINT PRIMARY KEY,
    user_name TEXT NOT NULL,
    created_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    comment TEXT,
    nodes_created INT DEFAULT 0,
    ways_created INT DEFAULT 0,
    relations_created INT DEFAULT 0,
    total_created INT DEFAULT 0,
    nodes_modified INT DEFAULT 0,
    ways_modified INT DEFAULT 0,
    relations_modified INT DEFAULT 0,
    total_modified INT DEFAULT 0,
    nodes_deleted INT DEFAULT 0,
    ways_deleted INT DEFAULT 0,
    relations_deleted INT DEFAULT 0,
    total_deleted INT DEFAULT 0,
    min_lat DOUBLE PRECISION,
    min_lon DOUBLE PRECISION,
    max_lat DOUBLE PRECISION,
    max_lon DOUBLE PRECISION,
    osmcha_suspect BOOLEAN,
    osmcha_reasons TEXT,
    osmcha_editor TEXT,
    synced_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_osm_changesets_user ON osm_changesets (user_name);
CREATE INDEX IF NOT EXISTS idx_osm_changesets_created ON osm_changesets (created_at);

CREATE TABLE IF NOT EXISTS osm_errors (
    id SERIAL PRIMARY KEY,
    changeset_id BIGINT,
    error_type TEXT,
    item_code TEXT,
    elem_type TEXT,
    elem_id BIGINT,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    synced_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_osm_errors_cs ON osm_errors (changeset_id);
