"""Add persistent entity memory tables.

Revision ID: 20260727_0001
Revises:
Create Date: 2026-07-27

Additive migration for entities / entity_observations / entity_snapshots.
Does not modify vision_runs / identity_* tables from 001_initial_schema.sql.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("identity_key", sa.String(length=255), nullable=False),
        sa.Column(
            "identity_strategy",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=True),
        sa.Column("camera_id", sa.String(length=128), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False),
        sa.Column("average_confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_bounding_box", sa.JSON(), nullable=True),
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
    )
    op.create_index(
        "ix_entities_identity_key",
        "entities",
        ["identity_key"],
        unique=False,
    )
    op.create_index(
        "ix_entities_status_last_seen",
        "entities",
        ["status", "last_seen"],
        unique=False,
    )
    op.create_index(
        "ix_entities_label",
        "entities",
        ["label"],
        unique=False,
    )

    op.create_table(
        "entity_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("camera_id", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("bounding_box", sa.JSON(), nullable=True),
        sa.Column("frame_number", sa.BigInteger(), nullable=True),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=True),
        sa.Column("source_event_type", sa.String(length=64), nullable=False),
        sa.Column("source_event_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id"),
    )
    op.create_index(
        "ix_entity_observations_entity_observed",
        "entity_observations",
        ["entity_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_entity_observations_camera_observed",
        "entity_observations",
        ["camera_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_entity_observations_frame_number",
        "entity_observations",
        ["frame_number"],
        unique=False,
    )
    op.create_index(
        "ix_entity_observations_source_event_id",
        "entity_observations",
        ["source_event_id"],
        unique=False,
    )

    op.create_table(
        "entity_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("identity_key", sa.String(length=255), nullable=False),
        sa.Column(
            "identity_strategy",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=True),
        sa.Column("camera_id", sa.String(length=128), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False),
        sa.Column("average_confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("bounding_box", sa.JSON(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entity_snapshots_entity_snapshot_at",
        "entity_snapshots",
        ["entity_id", "snapshot_at"],
        unique=False,
    )
    op.create_index(
        "ix_entity_snapshots_reason",
        "entity_snapshots",
        ["reason"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entity_snapshots_reason",
        table_name="entity_snapshots",
    )
    op.drop_index(
        "ix_entity_snapshots_entity_snapshot_at",
        table_name="entity_snapshots",
    )
    op.drop_table("entity_snapshots")

    op.drop_index(
        "ix_entity_observations_source_event_id",
        table_name="entity_observations",
    )
    op.drop_index(
        "ix_entity_observations_frame_number",
        table_name="entity_observations",
    )
    op.drop_index(
        "ix_entity_observations_camera_observed",
        table_name="entity_observations",
    )
    op.drop_index(
        "ix_entity_observations_entity_observed",
        table_name="entity_observations",
    )
    op.drop_table("entity_observations")

    op.drop_index("ix_entities_label", table_name="entities")
    op.drop_index("ix_entities_status_last_seen", table_name="entities")
    op.drop_index("ix_entities_identity_key", table_name="entities")
    op.drop_table("entities")
