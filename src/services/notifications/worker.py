"""Background worker for outbound notification delivery."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services.notifications.provider import DeliveryResult
from services.notifications.registry import NotificationProviderRegistry
from services.notifications.secrets import SecretEncryptionError, decrypt_secret
from storage.notification_orm import DeliveryStatus
from storage.notification_records import NotificationDeliveryRecord
from storage.notification_repositories import (
    NotificationDeliveryRepository,
    NotificationTargetRepository,
)


def compute_backoff_seconds(
    attempt_number: int,
    *,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    backoff_multiplier: float,
) -> float:
    """Bounded exponential backoff. attempt_number is 1-based after failure."""

    if attempt_number < 1:
        attempt_number = 1
    delay = initial_backoff_seconds * (
        backoff_multiplier ** (attempt_number - 1)
    )
    return min(max(delay, 0.0), max_backoff_seconds)


class NotificationDeliveryWorker:
    """Claim → deliver outside DB → record cycle."""

    def __init__(
        self,
        delivery_repository: NotificationDeliveryRepository,
        target_repository: NotificationTargetRepository,
        provider_registry: NotificationProviderRegistry,
        *,
        enabled: bool = True,
        worker_id: str = "jarvis-notification-worker",
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 5,
        initial_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 1800.0,
        backoff_multiplier: float = 2.0,
        batch_size: int = 50,
        max_concurrent_deliveries: int = 3,
        lock_timeout_seconds: float = 60.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._deliveries = delivery_repository
        self._targets = target_repository
        self._registry = provider_registry
        self.enabled = enabled
        self.worker_id = worker_id
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)
        self.max_attempts = max(1, max_attempts)
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.backoff_multiplier = backoff_multiplier
        self.batch_size = max(1, batch_size)
        self.max_concurrent_deliveries = max(1, max_concurrent_deliveries)
        self.lock_timeout_seconds = lock_timeout_seconds
        self._logger = logger or logging.getLogger(__name__)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._degraded = False
        # Lightweight counters
        self.delivered_total = 0
        self.failed_total = 0
        self.exhausted_total = 0
        self.retry_total = 0
        self.last_delivery_latency_ms: float | None = None
        self._latency_sum_ms = 0.0
        self._latency_count = 0

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def average_delivery_latency_ms(self) -> float | None:
        if self._latency_count == 0:
            return None
        return self._latency_sum_ms / self._latency_count

    def stats(self) -> dict[str, Any]:
        try:
            by_status = self._deliveries.counts_by_status()
        except Exception:  # noqa: BLE001
            by_status = {}
        return {
            "enabled": self.enabled,
            "ready": self.is_ready,
            "degraded": self.is_degraded,
            "worker_id": self.worker_id,
            "pending": by_status.get("pending", 0),
            "processing": by_status.get("processing", 0),
            "delivered_total": self.delivered_total,
            "failed_total": self.failed_total,
            "exhausted_total": self.exhausted_total,
            "retry_total": self.retry_total,
            "average_delivery_latency_ms": self.average_delivery_latency_ms,
            "last_delivery_latency_ms": self.last_delivery_latency_ms,
            "queue_depth_by_status": by_status,
        }

    async def start(self) -> None:
        if not self.enabled:
            self._ready.set()
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._ready.clear()
        self._task = asyncio.create_task(
            self._run(), name="notification-delivery-worker"
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._ready.clear()

    async def wait_until_ready(self, timeout: float = 30.0) -> bool:
        if not self.enabled:
            return True
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self) -> None:
        self._ready.set()
        self._logger.info(
            "Notification delivery worker started (%s)", self.worker_id
        )
        try:
            while not self._stop.is_set():
                try:
                    await self._cycle()
                    self._degraded = False
                except Exception:
                    self._degraded = True
                    self._logger.exception("Notification worker cycle failed")
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        finally:
            self._logger.info("Notification delivery worker stopped")

    async def _cycle(self) -> None:
        # Recover stale locks (transaction A-style local work)
        try:
            recovered = await asyncio.to_thread(
                self._deliveries.recover_stale_locks,
                lock_timeout_seconds=self.lock_timeout_seconds,
            )
            if recovered:
                self._logger.warning(
                    "Recovered %s stale notification delivery locks", recovered
                )
        except Exception:
            self._logger.exception("Stale lock recovery failed")

        claimed = await asyncio.to_thread(
            self._deliveries.claim_due,
            worker_id=self.worker_id,
            batch_size=self.batch_size,
        )
        if not claimed:
            return

        sem = asyncio.Semaphore(self.max_concurrent_deliveries)

        async def _one(delivery: NotificationDeliveryRecord) -> None:
            async with sem:
                await asyncio.to_thread(self._process_delivery, delivery)

        await asyncio.gather(*[_one(d) for d in claimed])

    def process_one_sync(
        self, delivery: NotificationDeliveryRecord | None = None
    ) -> int:
        """Synchronous single-cycle helper for tests. Returns processed count."""

        self._deliveries.recover_stale_locks(
            lock_timeout_seconds=self.lock_timeout_seconds
        )
        if delivery is None:
            claimed = self._deliveries.claim_due(
                worker_id=self.worker_id, batch_size=self.batch_size
            )
        else:
            claimed = [delivery]
        for item in claimed:
            self._process_delivery(item)
        return len(claimed)

    def _process_delivery(self, delivery: NotificationDeliveryRecord) -> None:
        target = self._targets.get_by_id(delivery.target_id)
        if target is None or not target.enabled:
            self._record(
                delivery,
                DeliveryResult(
                    success=False,
                    retryable=False,
                    error_code="target_unavailable",
                    error_message_sanitized="Target missing or disabled.",
                ),
            )
            return

        provider = self._registry.get_for_target(target)
        if provider is None:
            self._record(
                delivery,
                DeliveryResult(
                    success=False,
                    retryable=False,
                    error_code="no_provider",
                    error_message_sanitized="No provider for channel type.",
                ),
            )
            return

        signing_secret: str | None = None
        if target.has_signing_secret:
            row = self._targets.get_row_by_id(target.id)
            if row is not None and row.signing_secret_encrypted:
                try:
                    signing_secret = decrypt_secret(
                        row.signing_secret_encrypted
                    )
                except SecretEncryptionError as error:
                    self._record(
                        delivery,
                        DeliveryResult(
                            success=False,
                            retryable=False,
                            error_code="secret_decrypt_failed",
                            error_message_sanitized=str(error)[:512],
                        ),
                    )
                    return

        # Network outside any DB transaction
        result = provider.deliver(
            target,
            dict(delivery.payload),
            delivery.idempotency_key,
            signing_secret=signing_secret,
        )
        self._record(delivery, result)

    def _record(
        self,
        delivery: NotificationDeliveryRecord,
        result: DeliveryResult,
    ) -> None:
        now = datetime.now(timezone.utc)
        attempt_number = delivery.attempts + 1
        if result.duration_ms is not None:
            self.last_delivery_latency_ms = result.duration_ms
            self._latency_sum_ms += result.duration_ms
            self._latency_count += 1

        if result.success:
            new_status = DeliveryStatus.DELIVERED
            next_at = None
            self.delivered_total += 1
        elif not result.retryable:
            new_status = DeliveryStatus.EXHAUSTED
            next_at = None
            self.exhausted_total += 1
            self.failed_total += 1
        elif attempt_number >= self.max_attempts:
            new_status = DeliveryStatus.EXHAUSTED
            next_at = None
            self.exhausted_total += 1
            self.failed_total += 1
        else:
            # failed = retry scheduled
            new_status = DeliveryStatus.FAILED
            delay = compute_backoff_seconds(
                attempt_number,
                initial_backoff_seconds=self.initial_backoff_seconds,
                max_backoff_seconds=self.max_backoff_seconds,
                backoff_multiplier=self.backoff_multiplier,
            )
            next_at = now + timedelta(seconds=delay)
            self.retry_total += 1
            self.failed_total += 1

        self._deliveries.record_attempt_and_update(
            delivery.id,
            attempt_number=attempt_number,
            attempted_at=now,
            duration_ms=result.duration_ms,
            response_status=result.response_status,
            response_body_truncated=(
                (result.response_body_truncated or "")[:512] or None
            ),
            error_type=result.error_code,
            error_message_sanitized=result.error_message_sanitized,
            new_status=new_status,
            next_attempt_at=next_at,
            response_summary=result.response_summary,
            last_error=result.error_message_sanitized,
        )
