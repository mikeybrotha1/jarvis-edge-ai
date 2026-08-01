"""Add spatial zones and entity-zone sessions.

Revision ID: 20260801_0003
Revises: 20260728_0002
Create Date: 2026-08-01

Additive tables for Spatial Intelligence (v0.6.0).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0003"
down_revision: Union[str, Sequence[str], None] = "20260728_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zones",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("camera_id", sa.String(length=128), nullable=False),
        sa.Column("geometry_type", sa.String(length=32), nullable=False),
        sa.Column("vertices", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("entity_type_filters", sa.JSON(), nullable=False),
        sa.Column("min_confidence", sa.Float(), nullable=True),
        sa.Column("position_strategy", sa.String(length=32), nullable=True),
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
        sa.UniqueConstraint(
            "camera_id",
            "name",
            name="uq_zones_camera_id_name",
        ),
    )
    op.create_index("ix_zones_camera_id", "zones", ["camera_id"], unique=False)
    op.create_index("ix_zones_enabled", "zones", ["enabled"], unique=False)
    op.create_index(
        "ix_zones_camera_id_enabled",
        "zones",
        ["camera_id", "enabled"],
        unique=False,
    )

    op.create_table(
        "entity_zone_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("zone_id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("camera_id", sa.String(length=128), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("entry_event_id", sa.String(length=128), nullable=False),
        sa.Column("exit_event_id", sa.String(length=128), nullable=True),
        sa.Column("occupancy_after_enter", sa.Integer(), nullable=False),
        sa.Column("occupancy_after_exit", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zones.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ezs_zone_id_status",
        "entity_zone_sessions",
        ["zone_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_ezs_entity_id_status",
        "entity_zone_sessions",
        ["entity_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_ezs_camera_id_status",
        "entity_zone_sessions",
        ["camera_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_ezs_zone_id_entered_at",
        "entity_zone_sessions",
        ["zone_id", "entered_at"],
        unique=False,
    )
    op.create_index(
        "ix_ezs_entity_id_entered_at",
        "entity_zone_sessions",
        ["entity_id", "entered_at"],
        unique=False,
    )
    op.create_index(
        "ix_ezs_entered_at_id",
        "entity_zone_sessions",
        ["entered_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_ezs_exited_at_id",
        "entity_zone_sessions",
        ["exited_at", "id"],
        unique=False,
    )

    # Partial unique index: one open session per zone+entity.
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_ezs_open_zone_entity "
                "ON entity_zone_sessions (zone_id, entity_id) "
                "WHERE status = 'open'"
            )
        )
    else:
        # SQLite supports partial unique indexes.
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_ezs_open_zone_entity "
                "ON entity_zone_sessions (zone_id, entity_id) "
                "WHERE status = 'open'"
            )
        )


def downgrade() -> None:
    op.drop_index(
        "uq_ezs_open_zone_entity",
        table_name="entity_zone_sessions",
    )
    op.drop_index("ix_ezs_exited_at_id", table_name="entity_zone_sessions")
    op.drop_index("ix_ezs_entered_at_id", table_name="entity_zone_sessions")
    op.drop_index(
        "ix_ezs_entity_id_entered_at",
        table_name="entity_zone_sessions",
    )
    op.drop_index(
        "ix_ezs_zone_id_entered_at",
        table_name="entity_zone_sessions",
    )
    op.drop_index(
        "ix_ezs_camera_id_status",
        table_name="entity_zone_sessions",
    )
    op.drop_index(
        "ix_ezs_entity_id_status",
        table_name="entity_zone_sessions",
    )
    op.drop_index(
        "ix_ezs_zone_id_status",
        table_name="entity_zone_sessions",
    )
    op.drop_table("entity_zone_sessions")

    op.drop_index("ix_zones_camera_id_enabled", table_name="zones")
    op.drop_index("ix_zones_enabled", table_name="zones")
    op.drop_index("ix_zones_camera_id", table_name="zones")
    op.drop_table("zones")
