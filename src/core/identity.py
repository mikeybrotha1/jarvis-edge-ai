"""Pluggable identity matching for persistent entity memory.

Purpose
-------
Map raw object-lifecycle observations to a stable ``identity_key`` used by
entity memory. The default strategy scopes short-term tracker IDs by camera.
Future strategies (face recognition, CLIP embeddings, multi-camera
re-identification) implement the same protocol so repositories and
downstream consumers remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ObservationContext:
    """Minimal observation fields needed to resolve an identity key."""

    track_id: int
    label: str
    confidence: float
    bounding_box: dict[str, Any] | None = None
    camera_id: str | None = None
    frame_number: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IdentityMatch:
    """Result of resolving an observation to a persistent identity key.

    Attributes
    ----------
    identity_key:
        Opaque stable key stored on the entity row. Downstream code must treat
        this as opaque — never parse strategy-specific formats.
    strategy:
        Name of the matcher that produced the key (for auditing / migrations).
    track_id:
        Tracker ID when known (may be absent for future embedding strategies).
    label:
        Detector label associated with the observation.
    confidence:
        Detection confidence in ``[0, 1]``.
    metadata:
        Optional strategy-specific payload (embedding ids, face ids, …).
    """

    identity_key: str
    strategy: str
    track_id: int | None = None
    label: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class IdentityMatcher(Protocol):
    """Resolve observations to persistent identity keys."""

    @property
    def strategy_name(self) -> str:
        """Machine-readable strategy identifier."""

    def match(self, observation: ObservationContext) -> IdentityMatch:
        """Return the identity match for one observation."""


class TrackerIdIdentityMatcher:
    """Identity matcher scoped by camera and short-term tracker ID.

    Key format: ``camera:{camera_id}:tracker:{track_id}``

    Camera scoping prevents collisions when the same tracker ID is reused on
    different cameras. This remains appearance-local: a later
    ``OBJECT_ENTERED`` with the same camera/tracker pair creates a *new*
    entity row rather than reopening a closed one. Higher-level matchers can
    later associate multiple entity rows with one real-world object.
    """

    STRATEGY_NAME = "tracker_id"

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    def match(self, observation: ObservationContext) -> IdentityMatch:
        if observation.track_id < 0:
            raise ValueError("track_id cannot be negative")

        camera_id = (observation.camera_id or "").strip()
        if not camera_id:
            raise ValueError(
                "camera_id is required for tracker identity matching"
            )

        identity_key = (
            f"camera:{camera_id}:tracker:{observation.track_id}"
        )

        return IdentityMatch(
            identity_key=identity_key,
            strategy=self.strategy_name,
            track_id=observation.track_id,
            label=observation.label,
            confidence=observation.confidence,
            metadata={
                "camera_id": camera_id,
                "frame_number": observation.frame_number,
            },
        )


def build_identity_matcher(strategy: str) -> IdentityMatcher:
    """Return an identity matcher for a configured strategy name.

    Currently only ``tracker_id`` is implemented. Unknown strategies raise
    ``ValueError`` so misconfiguration fails fast at startup.
    """

    name = strategy.strip().lower()
    if name == TrackerIdIdentityMatcher.STRATEGY_NAME:
        return TrackerIdIdentityMatcher()

    raise ValueError(
        f"Unknown entity memory identity strategy: {strategy!r}. "
        f"Supported: {TrackerIdIdentityMatcher.STRATEGY_NAME!r}."
    )
