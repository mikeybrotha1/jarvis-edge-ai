"""Add durable alerts and rule evaluation tables.

Revision ID: 20260802_0004
Revises: 20260801_0003
Create Date: 2026-08-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0004"
down_revision: Union[str, Sequence[str], None] = "20260801_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source_event_types", sa.JSON(), nullable=False),
        sa.Column("camera_ids", sa.JSON(), nullable=False),
        sa.Column("zone_ids", sa.JSON(), nullable=False),
        sa.Column("entity_types", sa.JSON(), nullable=False),
        sa.Column("occupancy_threshold", sa.Integer(), nullable=True),
        sa.Column("occupancy_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("dwell_threshold_seconds", sa.Integer(), nullable=True),
        sa.Column("active_window_start", sa.String(8), nullable=True),
        sa.Column("active_window_end", sa.String(8), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("days_of_week", sa.JSON(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_alert_rules_name"),
    )
    op.create_index("ix_alert_rules_enabled", "alert_rules", ["enabled"])
    op.create_index("ix_alert_rules_rule_type", "alert_rules", ["rule_type"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("zone_id", sa.String(36), nullable=True),
        sa.Column("camera_id", sa.String(128), nullable=True),
        sa.Column("source_event_id", sa.String(256), nullable=False),
        sa.Column("subject_key", sa.String(512), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_alerts_idempotency_key"),
    )
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_rule_id_status", "alerts", ["rule_id", "status"])
    op.create_index("ix_alerts_entity_id", "alerts", ["entity_id"])
    op.create_index("ix_alerts_zone_id", "alerts", ["zone_id"])
    op.create_index("ix_alerts_triggered_at_id", "alerts", ["triggered_at", "id"])
    op.create_index("ix_alerts_resolved_at_id", "alerts", ["resolved_at", "id"])
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_alerts_open_rule_subject "
            "ON alerts (rule_id, subject_key) "
            "WHERE status IN ('open', 'acknowledged')"
        )
    )

    op.create_table(
        "alert_evaluator_state",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("subject_key", sa.String(512), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("zone_id", sa.String(36), nullable=True),
        sa.Column("source_event_id", sa.String(256), nullable=False),
        sa.Column(
            "condition_started_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id", "subject_key", name="uq_aes_rule_subject"
        ),
    )
    op.create_index(
        "ix_aes_due_at_state", "alert_evaluator_state", ["due_at", "state"]
    )
    op.create_index(
        "ix_aes_rule_id_subject_key",
        "alert_evaluator_state",
        ["rule_id", "subject_key"],
    )
    op.create_index("ix_aes_entity_id", "alert_evaluator_state", ["entity_id"])
    op.create_index("ix_aes_zone_id", "alert_evaluator_state", ["zone_id"])

    op.create_table(
        "alert_evaluator_checkpoint",
        sa.Column("consumer_name", sa.String(128), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_id", sa.String(256), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("consumer_name"),
    )


def downgrade() -> None:
    op.drop_table("alert_evaluator_checkpoint")
    op.drop_index("ix_aes_zone_id", table_name="alert_evaluator_state")
    op.drop_index("ix_aes_entity_id", table_name="alert_evaluator_state")
    op.drop_index("ix_aes_rule_id_subject_key", table_name="alert_evaluator_state")
    op.drop_index("ix_aes_due_at_state", table_name="alert_evaluator_state")
    op.drop_table("alert_evaluator_state")
    op.execute(sa.text("DROP INDEX IF EXISTS uq_alerts_open_rule_subject"))
    op.drop_index("ix_alerts_resolved_at_id", table_name="alerts")
    op.drop_index("ix_alerts_triggered_at_id", table_name="alerts")
    op.drop_index("ix_alerts_zone_id", table_name="alerts")
    op.drop_index("ix_alerts_entity_id", table_name="alerts")
    op.drop_index("ix_alerts_rule_id_status", table_name="alerts")
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_alert_rules_rule_type", table_name="alert_rules")
    op.drop_index("ix_alert_rules_enabled", table_name="alert_rules")
    op.drop_table("alert_rules")
