"""Timeline provider architecture (v0.7.0).

Domain providers project events; TimelineComposer merges bounded streams.
Public TimelineEvent / REST / WebSocket behaviour matches v0.6.0.
"""

from __future__ import annotations

from timeline.composer import TimelineComposer, TimelineProviderRegistrationError
from timeline.contracts import (
    TIMELINE_UNION_COLUMN_NAMES,
    null_projection_defaults,
    projection,
    row_to_timeline_event,
)
from timeline.factory import build_default_timeline_composer, build_default_timeline_providers
from timeline.provider import TimelineProvider, TimelineQueryContext

__all__ = [
    "TIMELINE_UNION_COLUMN_NAMES",
    "TimelineComposer",
    "TimelineProvider",
    "TimelineProviderRegistrationError",
    "TimelineQueryContext",
    "build_default_timeline_composer",
    "build_default_timeline_providers",
    "null_projection_defaults",
    "projection",
    "row_to_timeline_event",
]
