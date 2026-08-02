"""Pydantic schemas for operational observability endpoints (v0.10.0)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReadyOut(BaseModel):
    ready: bool
    status: str
    timestamp: str
    checks: dict[str, str] = Field(default_factory=dict)


class OpsStatusOut(BaseModel):
    """Bounded operational status document (JSON)."""

    status: str
    service: str
    timestamp: str
    components: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    # Additive v0.10.0 phase 3+: policy / worker snapshot.
    retention: dict[str, Any] | None = None


class RetentionDomainPolicyOut(BaseModel):
    enabled: bool
    keep_days: int | None = None
    keep_closed_days: int | None = None
    keep_resolved_days: int | None = None
    keep_inactive_days: int | None = None
    keep_terminal_days: int | None = None


class RetentionDomainResultOut(BaseModel):
    domain: str
    dry_run: bool
    cutoff: str
    eligible_total: int
    batches: int
    rows_examined: int
    rows_deleted: int
    rows_skipped: int
    duration_ms: float
    status: str
    error: str | None = None


class RetentionRunSummaryOut(BaseModel):
    dry_run: bool
    started_at: str
    completed_at: str
    duration_ms: float
    rows_examined: int
    rows_deleted: int
    rows_skipped: int
    status: str
    error: str | None = None
    domains: list[RetentionDomainResultOut] = Field(default_factory=list)


class RetentionStatusOut(BaseModel):
    """GET /api/v1/ops/retention"""

    enabled: bool
    dry_run: bool
    allow_manual_destructive_run: bool
    destructive_permitted: bool
    interval_seconds: int = 86400
    batch_size: int = 250
    max_batches_per_run: int = 4
    manual_cooldown_seconds: float = 30.0
    cooldown_remaining_seconds: float = 0.0
    any_domain_enabled: bool = False
    worker: dict[str, Any] = Field(default_factory=dict)
    domains: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class RetentionManualTriggerOut(BaseModel):
    """POST dry-run / run response."""

    accepted: bool = True
    trigger: str  # dry_run | run
    summary: RetentionRunSummaryOut
