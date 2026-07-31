-- Persistent entity memory tables (v0.4.0).
-- Kept in sync with alembic/versions/20260727_0001_add_entity_memory.py
-- for environments that bootstrap schema via plain SQL.

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL,
    identity_strategy TEXT NOT NULL DEFAULT 'tracker_id',
    label TEXT NOT NULL,
    track_id BIGINT,
    camera_id TEXT,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1 CHECK (times_seen >= 1),
    average_confidence DOUBLE PRECISION NOT NULL
        CHECK (average_confidence BETWEEN 0.0 AND 1.0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'closed')),
    last_bounding_box JSONB,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS entity_observations (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    camera_id TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL
        CHECK (confidence BETWEEN 0.0 AND 1.0),
    bounding_box JSONB,
    frame_number BIGINT,
    label TEXT NOT NULL,
    track_id BIGINT,
    source_event_type TEXT NOT NULL,
    source_event_id TEXT UNIQUE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS entity_snapshots (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    snapshot_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    identity_strategy TEXT NOT NULL,
    label TEXT NOT NULL,
    track_id BIGINT,
    camera_id TEXT,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    times_seen INTEGER NOT NULL,
    average_confidence DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'closed')),
    bounding_box JSONB,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_entities_identity_key
    ON entities (identity_key);
CREATE INDEX IF NOT EXISTS ix_entities_status_last_seen
    ON entities (status, last_seen DESC);
CREATE INDEX IF NOT EXISTS ix_entities_label
    ON entities (label);

CREATE INDEX IF NOT EXISTS ix_entity_observations_entity_observed
    ON entity_observations (entity_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_entity_observations_camera_observed
    ON entity_observations (camera_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS ix_entity_observations_frame_number
    ON entity_observations (frame_number);
CREATE INDEX IF NOT EXISTS ix_entity_observations_source_event_id
    ON entity_observations (source_event_id);

CREATE INDEX IF NOT EXISTS ix_entity_snapshots_entity_snapshot_at
    ON entity_snapshots (entity_id, snapshot_at);
CREATE INDEX IF NOT EXISTS ix_entity_snapshots_reason
    ON entity_snapshots (reason);
