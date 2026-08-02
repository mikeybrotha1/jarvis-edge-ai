-- Outbound notification delivery (v0.9.0).
-- Kept in sync with alembic/versions/20260802_0005_outbound_notifications.py

CREATE TABLE IF NOT EXISTS notification_targets (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    channel_type VARCHAR(32) NOT NULL,
    url VARCHAR(2048) NOT NULL,
    enabled BOOLEAN NOT NULL,
    is_global BOOLEAN NOT NULL,
    signing_secret_encrypted TEXT,
    severity_filters JSON NOT NULL,
    extra JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_notification_targets_enabled
    ON notification_targets (enabled);
CREATE INDEX IF NOT EXISTS ix_notification_targets_channel_type
    ON notification_targets (channel_type);
CREATE INDEX IF NOT EXISTS ix_notification_targets_is_global
    ON notification_targets (is_global);

CREATE TABLE IF NOT EXISTS rule_notification_targets (
    id VARCHAR(36) PRIMARY KEY,
    rule_id VARCHAR(36) NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    target_id VARCHAR(36) NOT NULL REFERENCES notification_targets(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (rule_id, target_id)
);
CREATE INDEX IF NOT EXISTS ix_rnt_rule_id ON rule_notification_targets (rule_id);
CREATE INDEX IF NOT EXISTS ix_rnt_target_id ON rule_notification_targets (target_id);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id VARCHAR(36) PRIMARY KEY,
    alert_id VARCHAR(36) NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    target_id VARCHAR(36) NOT NULL REFERENCES notification_targets(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(512) NOT NULL UNIQUE,
    payload JSON NOT NULL,
    status VARCHAR(16) NOT NULL,
    attempts INTEGER NOT NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    locked_at TIMESTAMPTZ,
    locked_by VARCHAR(128),
    first_attempt_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    exhausted_at TIMESTAMPTZ,
    response_status INTEGER,
    response_summary VARCHAR(512),
    last_error VARCHAR(512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_nd_status_next_attempt
    ON notification_deliveries (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_nd_alert_id ON notification_deliveries (alert_id);
CREATE INDEX IF NOT EXISTS ix_nd_target_id ON notification_deliveries (target_id);
CREATE INDEX IF NOT EXISTS ix_nd_locked_at ON notification_deliveries (locked_at);

CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
    id VARCHAR(36) PRIMARY KEY,
    delivery_id VARCHAR(36) NOT NULL
        REFERENCES notification_deliveries(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL,
    duration_ms FLOAT,
    response_status INTEGER,
    response_body_truncated VARCHAR(512),
    error_type VARCHAR(64),
    error_message_sanitized VARCHAR(512)
);
CREATE INDEX IF NOT EXISTS ix_nda_delivery_attempt
    ON notification_delivery_attempts (delivery_id, attempt_number);
