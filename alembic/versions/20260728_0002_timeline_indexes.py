"""Add indexes for activity timeline projections.

Revision ID: 20260728_0002
Revises: 20260727_0001
Create Date: 2026-07-28

Additive indexes only. No timeline_events table and no lifecycle schema changes.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260728_0002"
down_revision: Union[str, Sequence[str], None] = "20260727_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_entities_first_seen_id",
        "entities",
        ["first_seen", "id"],
        unique=False,
    )
    op.create_index(
        "ix_entities_status_last_seen_id",
        "entities",
        ["status", "last_seen", "id"],
        unique=False,
    )
    op.create_index(
        "ix_entities_camera_first_seen_id",
        "entities",
        ["camera_id", "first_seen", "id"],
        unique=False,
    )
    op.create_index(
        "ix_entities_camera_last_seen_id",
        "entities",
        ["camera_id", "last_seen", "id"],
        unique=False,
    )

    op.create_index(
        "ix_entity_observations_observed_at_id",
        "entity_observations",
        ["observed_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_entity_observations_entity_observed_id",
        "entity_observations",
        ["entity_id", "observed_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_entity_observations_camera_observed_id",
        "entity_observations",
        ["camera_id", "observed_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entity_observations_camera_observed_id",
        table_name="entity_observations",
    )
    op.drop_index(
        "ix_entity_observations_entity_observed_id",
        table_name="entity_observations",
    )
    op.drop_index(
        "ix_entity_observations_observed_at_id",
        table_name="entity_observations",
    )
    op.drop_index(
        "ix_entities_camera_last_seen_id",
        table_name="entities",
    )
    op.drop_index(
        "ix_entities_camera_first_seen_id",
        table_name="entities",
    )
    op.drop_index(
        "ix_entities_status_last_seen_id",
        table_name="entities",
    )
    op.drop_index(
        "ix_entities_first_seen_id",
        table_name="entities",
    )
