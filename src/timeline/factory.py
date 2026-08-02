"""Factories for default timeline providers and composer (v0.7.0)."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from timeline.composer import TimelineComposer
from timeline.provider import TimelineProvider
from timeline.providers.entity_lifecycle import EntityLifecycleTimelineProvider
from timeline.providers.spatial import SpatialTimelineProvider


def build_default_timeline_providers(
    session_factory: sessionmaker[Session],
) -> list[TimelineProvider]:
    """Construct the default ordered provider set for production."""

    return [
        EntityLifecycleTimelineProvider(session_factory),
        SpatialTimelineProvider(session_factory),
    ]


def build_default_timeline_composer(
    session_factory: sessionmaker[Session],
) -> TimelineComposer:
    """Build a composer with lifecycle + spatial providers registered."""

    return TimelineComposer(build_default_timeline_providers(session_factory))
