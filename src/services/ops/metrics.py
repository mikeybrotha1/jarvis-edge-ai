"""Bounded in-process operational metrics (v0.10.0 phase 2).

Design
------
- Fixed, low-cardinality metric names only (no per-entity/rule/target labels).
- Counters are monotonic and clamped to a maximum to avoid unbounded growth
  in long-running processes for reporting purposes (values still increase
  until clamp).
- Gauges store latest samples only.
- Timestamps store last-success / last-error instants.
- Not a Prometheus registry; optional scrape can be added later and remains
  disabled by default.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Hard caps keep memory and JSON payloads bounded.
_MAX_COUNTER_KEYS = 64
_MAX_GAUGE_KEYS = 64
_MAX_TIMESTAMP_KEYS = 64
_MAX_COUNTER_VALUE = 2**63 - 1
_LATENCY_WINDOW = 32  # samples for moving average


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _LatencyWindow:
    samples: list[float] = field(default_factory=list)

    def add(self, value_ms: float) -> None:
        self.samples.append(float(value_ms))
        if len(self.samples) > _LATENCY_WINDOW:
            self.samples = self.samples[-_LATENCY_WINDOW:]

    @property
    def average_ms(self) -> float | None:
        if not self.samples:
            return None
        return sum(self.samples) / len(self.samples)

    @property
    def last_ms(self) -> float | None:
        if not self.samples:
            return None
        return self.samples[-1]


class OpsMetricsRegistry:
    """Process-local metrics snapshot source for /api/v1/ops/status."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._last_success: dict[str, datetime] = {}
        self._last_error: dict[str, datetime] = {}
        self._latencies: dict[str, _LatencyWindow] = {}
        self._started_at = _utc_now()
        self._started_mono = time.monotonic()

    def inc(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            if name not in self._counters and len(self._counters) >= _MAX_COUNTER_KEYS:
                return
            current = self._counters.get(name, 0)
            self._counters[name] = min(current + amount, _MAX_COUNTER_VALUE)

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            if name not in self._gauges and len(self._gauges) >= _MAX_GAUGE_KEYS:
                return
            self._gauges[name] = float(value)

    def mark_success(self, name: str) -> None:
        with self._lock:
            if (
                name not in self._last_success
                and len(self._last_success) >= _MAX_TIMESTAMP_KEYS
            ):
                return
            self._last_success[name] = _utc_now()

    def mark_error(self, name: str) -> None:
        with self._lock:
            if (
                name not in self._last_error
                and len(self._last_error) >= _MAX_TIMESTAMP_KEYS
            ):
                return
            self._last_error[name] = _utc_now()
            if name not in self._counters and len(self._counters) >= _MAX_COUNTER_KEYS:
                return
            current = self._counters.get(f"{name}_errors", 0)
            self._counters[f"{name}_errors"] = min(
                current + 1, _MAX_COUNTER_VALUE
            )

    def observe_latency_ms(self, name: str, duration_ms: float) -> None:
        with self._lock:
            if name not in self._latencies:
                if len(self._latencies) >= _MAX_GAUGE_KEYS:
                    return
                self._latencies[name] = _LatencyWindow()
            self._latencies[name].add(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe, bounded metrics document."""

        with self._lock:
            latencies: dict[str, dict[str, float | None]] = {}
            for key, window in self._latencies.items():
                latencies[key] = {
                    "last_ms": window.last_ms,
                    "average_ms": window.average_ms,
                    "samples": len(window.samples),
                }
            return {
                "uptime_seconds": round(
                    time.monotonic() - self._started_mono, 3
                ),
                "started_at": self._started_at.isoformat(),
                "counters": dict(sorted(self._counters.items())),
                "gauges": dict(sorted(self._gauges.items())),
                "last_success_at": {
                    k: v.isoformat()
                    for k, v in sorted(self._last_success.items())
                },
                "last_error_at": {
                    k: v.isoformat()
                    for k, v in sorted(self._last_error.items())
                },
                "latencies_ms": latencies,
                "bounds": {
                    "max_counter_keys": _MAX_COUNTER_KEYS,
                    "max_gauge_keys": _MAX_GAUGE_KEYS,
                    "max_timestamp_keys": _MAX_TIMESTAMP_KEYS,
                    "latency_window_samples": _LATENCY_WINDOW,
                },
            }
