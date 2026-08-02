-- Durable alerts & rule evaluation (v0.8.0).
-- Kept in sync with alembic/versions/20260802_0004_durable_alerts.py

CREATE TABLE IF NOT EXISTS alert_rules (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    rule_type VARCHAR(32) NOT NULL,
    enabled BOOLEAN NOT NULL,
    source_event_types JSON NOT NULL,
    camera_ids JSON NOT NULL,
    zone_ids JSON NOT NULL,
    entity_types JSON NOT NULL,
    occupancy_threshold INTEGER,
    occupancy_duration_seconds INTEGER,
    dwell_threshold_seconds INTEGER,
    active_window_start VARCHAR(8),
    active_window_end VARCHAR(8),
    timezone VARCHAR(64) NOT NULL,
    days_of_week JSON NOT NULL,
    cooldown_seconds INTEGER NOT NULL,
    severity VARCHAR(16) NOT NULL,
    extra JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_alert_rules_enabled ON alert_rules (enabled);
CREATE INDEX IF NOT EXISTS ix_alert_rules_rule_type ON alert_rules (rule_type);

CREATE TABLE IF NOT EXISTS alerts (
    id VARCHAR(36) PRIMARY KEY,
    rule_id VARCHAR(36) NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    entity_id VARCHAR(36) NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    zone_id VARCHAR(36) REFERENCES zones(id) ON DELETE SET NULL,
    camera_id VARCHAR(128),
    source_event_id VARCHAR(256) NOT NULL,
    subject_key VARCHAR(512) NOT NULL,
    idempotency_key VARCHAR(512) NOT NULL UNIQUE,
    triggered_at TIMESTAMPTZ NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    last_matched_at TIMESTAMPTZ NOT NULL,
    summary VARCHAR(512) NOT NULL,
    payload JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts (status);
CREATE INDEX IF NOT EXISTS ix_alerts_rule_id_status ON alerts (rule_id, status);
CREATE INDEX IF NOT EXISTS ix_alerts_entity_id ON alerts (entity_id);
CREATE INDEX IF NOT EXISTS ix_alerts_zone_id ON alerts (zone_id);
CREATE INDEX IF NOT EXISTS ix_alerts_triggered_at_id ON alerts (triggered_at, id);
CREATE INDEX IF NOT EXISTS ix_alerts_resolved_at_id ON alerts (resolved_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_open_rule_subject
    ON alerts (rule_id, subject_key)
    WHERE status IN ('open', 'acknowledged');

CREATE TABLE IF NOT EXISTS alert_evaluator_state (
    id VARCHAR(36) PRIMARY KEY,
    rule_id VARCHAR(36) NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    subject_key VARCHAR(512) NOT NULL,
    entity_id VARCHAR(36) NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    zone_id VARCHAR(36),
    source_event_id VARCHAR(256) NOT NULL,
    condition_started_at TIMESTAMPTZ NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    state VARCHAR(16) NOT NULL,
    alert_id VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (rule_id, subject_key)
);
CREATE INDEX IF NOT EXISTS ix_aes_due_at_state
    ON alert_evaluator_state (due_at, state);
CREATE INDEX IF NOT EXISTS ix_aes_rule_id_subject_key
    ON alert_evaluator_state (rule_id, subject_key);
CREATE INDEX IF NOT EXISTS ix_aes_entity_id ON alert_evaluator_state (entity_id);
CREATE INDEX IF NOT EXISTS ix_aes_zone_id ON alert_evaluator_state (zone_id);

CREATE TABLE IF NOT EXISTS alert_evaluator_checkpoint (
    consumer_name VARCHAR(128) PRIMARY KEY,
    last_occurred_at TIMESTAMPTZ,
    last_event_id VARCHAR(256),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
