-- Spatial Intelligence tables (v0.6.0).
-- Kept in sync with alembic/versions/20260801_0003_spatial_intelligence.py

CREATE TABLE IF NOT EXISTS zones (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    camera_id VARCHAR(128) NOT NULL,
    geometry_type VARCHAR(32) NOT NULL,
    vertices JSON NOT NULL,
    enabled BOOLEAN NOT NULL,
    entity_type_filters JSON NOT NULL,
    min_confidence FLOAT,
    position_strategy VARCHAR(32),
    extra JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_zones_camera_id_name UNIQUE (camera_id, name)
);

CREATE INDEX IF NOT EXISTS ix_zones_camera_id ON zones (camera_id);
CREATE INDEX IF NOT EXISTS ix_zones_enabled ON zones (enabled);
CREATE INDEX IF NOT EXISTS ix_zones_camera_id_enabled
    ON zones (camera_id, enabled);

CREATE TABLE IF NOT EXISTS entity_zone_sessions (
    id VARCHAR(36) PRIMARY KEY,
    zone_id VARCHAR(36) NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    entity_id VARCHAR(36) NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    camera_id VARCHAR(128) NOT NULL,
    entered_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    exited_at TIMESTAMPTZ,
    status VARCHAR(16) NOT NULL,
    entry_event_id VARCHAR(128) NOT NULL,
    exit_event_id VARCHAR(128),
    occupancy_after_enter INTEGER NOT NULL,
    occupancy_after_exit INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ezs_zone_id_status
    ON entity_zone_sessions (zone_id, status);
CREATE INDEX IF NOT EXISTS ix_ezs_entity_id_status
    ON entity_zone_sessions (entity_id, status);
CREATE INDEX IF NOT EXISTS ix_ezs_camera_id_status
    ON entity_zone_sessions (camera_id, status);
CREATE INDEX IF NOT EXISTS ix_ezs_zone_id_entered_at
    ON entity_zone_sessions (zone_id, entered_at);
CREATE INDEX IF NOT EXISTS ix_ezs_entity_id_entered_at
    ON entity_zone_sessions (entity_id, entered_at);
CREATE INDEX IF NOT EXISTS ix_ezs_entered_at_id
    ON entity_zone_sessions (entered_at, id);
CREATE INDEX IF NOT EXISTS ix_ezs_exited_at_id
    ON entity_zone_sessions (exited_at, id);

-- At most one open session per zone+entity.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ezs_open_zone_entity
    ON entity_zone_sessions (zone_id, entity_id)
    WHERE status = 'open';
