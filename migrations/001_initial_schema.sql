CREATE TABLE IF NOT EXISTS vision_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMPTZ,
    hostname TEXT NOT NULL,
    camera_source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    frames_processed BIGINT NOT NULL DEFAULT 0 CHECK (frames_processed >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS identity_sessions (
    run_id UUID NOT NULL REFERENCES vision_runs(run_id) ON DELETE CASCADE,
    identity TEXT NOT NULL,
    track_id BIGINT NOT NULL,
    label TEXT NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    appearance_count INTEGER NOT NULL DEFAULT 1 CHECK (appearance_count >= 1),
    total_frames_seen BIGINT NOT NULL DEFAULT 0 CHECK (total_frames_seen >= 0),
    highest_confidence DOUBLE PRECISION NOT NULL
        CHECK (highest_confidence BETWEEN 0.0 AND 1.0),
    last_confidence DOUBLE PRECISION NOT NULL
        CHECK (last_confidence BETWEEN 0.0 AND 1.0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_bounding_box JSONB,
    PRIMARY KEY (run_id, identity)
);

CREATE TABLE IF NOT EXISTS identity_events (
    event_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES vision_runs(run_id) ON DELETE CASCADE,
    identity TEXT NOT NULL,
    track_id BIGINT NOT NULL,
    label TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('object_entered', 'object_updated', 'object_exited')),
    observed_at TIMESTAMPTZ NOT NULL,
    confidence DOUBLE PRECISION NOT NULL
        CHECK (confidence BETWEEN 0.0 AND 1.0),
    frames_seen BIGINT NOT NULL DEFAULT 0 CHECK (frames_seen >= 0),
    bounding_box JSONB,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS frame_metrics (
    run_id UUID NOT NULL REFERENCES vision_runs(run_id) ON DELETE CASCADE,
    frame_id BIGINT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    fps DOUBLE PRECISION,
    detection_count INTEGER NOT NULL DEFAULT 0 CHECK (detection_count >= 0),
    processing_ms DOUBLE PRECISION,
    PRIMARY KEY (run_id, frame_id)
);

CREATE INDEX IF NOT EXISTS idx_identity_events_observed_at
    ON identity_events (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_events_identity
    ON identity_events (identity, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_events_label
    ON identity_events (label, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_sessions_active
    ON identity_sessions (active, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_frame_metrics_observed_at
    ON frame_metrics (observed_at DESC);
