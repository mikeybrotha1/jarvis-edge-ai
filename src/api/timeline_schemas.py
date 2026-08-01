"""Pydantic schemas for the timeline API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from storage.timeline_models import TimelineEvent, TimelineEventType, TimelinePage


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class TimelineEventOut(BaseModel):
    """Serialised timeline event."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: TimelineEventType
    occurred_at: datetime
    source: str
    entity_id: UUID
    camera_id: str | None = None
    entity_type: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_event(cls, event: TimelineEvent) -> "TimelineEventOut":
        return cls(
            id=event.id,
            event_type=event.event_type,
            occurred_at=_ensure_aware(event.occurred_at),
            source=event.source,
            entity_id=event.entity_id,
            camera_id=event.camera_id,
            entity_type=event.entity_type,
            summary=event.summary,
            payload=dict(event.payload),
        )


class TimelinePageOut(BaseModel):
    """Cursor-paginated timeline page."""

    items: list[TimelineEventOut]
    limit: int
    next_cursor: str | None = None

    @classmethod
    def from_page(cls, page: TimelinePage) -> "TimelinePageOut":
        return cls(
            items=[TimelineEventOut.from_event(item) for item in page.items],
            limit=page.limit,
            next_cursor=page.next_cursor,
        )
