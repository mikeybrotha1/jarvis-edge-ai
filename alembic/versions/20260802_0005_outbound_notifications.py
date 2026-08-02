"""Add outbound notification delivery tables.

Revision ID: 20260802_0005
Revises: 20260802_0004
Create Date: 2026-08-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0005"
down_revision: Union[str, Sequence[str], None] = "20260802_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_targets",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_global", sa.Boolean(), nullable=False),
        sa.Column("signing_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("severity_filters", sa.JSON(), nullable=False),
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
        sa.UniqueConstraint("name", name="uq_notification_targets_name"),
    )
    op.create_index(
        "ix_notification_targets_enabled", "notification_targets", ["enabled"]
    )
    op.create_index(
        "ix_notification_targets_channel_type",
        "notification_targets",
        ["channel_type"],
    )
    op.create_index(
        "ix_notification_targets_is_global",
        "notification_targets",
        ["is_global"],
    )

    op.create_table(
        "rule_notification_targets",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("rule_id", sa.String(36), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["alert_rules.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["notification_targets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id", "target_id", name="uq_rule_notification_targets"
        ),
    )
    op.create_index("ix_rnt_rule_id", "rule_notification_targets", ["rule_id"])
    op.create_index(
        "ix_rnt_target_id", "rule_notification_targets", ["target_id"]
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(128), nullable=True),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_summary", sa.String(512), nullable=True),
        sa.Column("last_error", sa.String(512), nullable=True),
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
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["target_id"], ["notification_targets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_notification_deliveries_idempotency"
        ),
    )
    op.create_index(
        "ix_nd_status_next_attempt",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_nd_alert_id", "notification_deliveries", ["alert_id"])
    op.create_index("ix_nd_target_id", "notification_deliveries", ["target_id"])
    op.create_index("ix_nd_locked_at", "notification_deliveries", ["locked_at"])

    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("delivery_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body_truncated", sa.String(512), nullable=True),
        sa.Column("error_type", sa.String(64), nullable=True),
        sa.Column("error_message_sanitized", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["notification_deliveries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nda_delivery_attempt",
        "notification_delivery_attempts",
        ["delivery_id", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nda_delivery_attempt", table_name="notification_delivery_attempts"
    )
    op.drop_table("notification_delivery_attempts")
    op.drop_index("ix_nd_locked_at", table_name="notification_deliveries")
    op.drop_index("ix_nd_target_id", table_name="notification_deliveries")
    op.drop_index("ix_nd_alert_id", table_name="notification_deliveries")
    op.drop_index(
        "ix_nd_status_next_attempt", table_name="notification_deliveries"
    )
    op.drop_table("notification_deliveries")
    op.drop_index("ix_rnt_target_id", table_name="rule_notification_targets")
    op.drop_index("ix_rnt_rule_id", table_name="rule_notification_targets")
    op.drop_table("rule_notification_targets")
    op.drop_index(
        "ix_notification_targets_is_global", table_name="notification_targets"
    )
    op.drop_index(
        "ix_notification_targets_channel_type",
        table_name="notification_targets",
    )
    op.drop_index(
        "ix_notification_targets_enabled", table_name="notification_targets"
    )
    op.drop_table("notification_targets")
