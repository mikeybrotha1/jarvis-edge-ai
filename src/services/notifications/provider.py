"""Notification provider protocol and delivery result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Safe, normalized outcome of one network delivery attempt."""

    success: bool
    retryable: bool
    response_status: int | None = None
    response_summary: str | None = None
    error_code: str | None = None
    error_message_sanitized: str | None = None
    duration_ms: float | None = None
    response_body_truncated: str | None = None


@runtime_checkable
class NotificationProvider(Protocol):
    """Channel-specific delivery backend. Must not use database sessions."""

    channel_type: str

    def supports(self, target: Any) -> bool:
        """Return True if this provider can deliver to the target."""

    def deliver(
        self,
        target: Any,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        signing_secret: str | None = None,
    ) -> DeliveryResult:
        """Perform one outbound delivery attempt."""
