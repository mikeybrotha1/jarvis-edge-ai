-- Timeline projection indexes (v0.4.2).
-- Kept in sync with alembic/versions/20260728_0002_timeline_indexes.py

CREATE INDEX IF NOT EXISTS ix_entities_first_seen_id
    ON entities (first_seen, id);
CREATE INDEX IF NOT EXISTS ix_entities_status_last_seen_id
    ON entities (status, last_seen, id);
CREATE INDEX IF NOT EXISTS ix_entities_camera_first_seen_id
    ON entities (camera_id, first_seen, id);
CREATE INDEX IF NOT EXISTS ix_entities_camera_last_seen_id
    ON entities (camera_id, last_seen, id);

CREATE INDEX IF NOT EXISTS ix_entity_observations_observed_at_id
    ON entity_observations (observed_at, id);
CREATE INDEX IF NOT EXISTS ix_entity_observations_entity_observed_id
    ON entity_observations (entity_id, observed_at, id);
CREATE INDEX IF NOT EXISTS ix_entity_observations_camera_observed_id
    ON entity_observations (camera_id, observed_at, id);
