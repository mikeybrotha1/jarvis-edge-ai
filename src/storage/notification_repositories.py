"""Repositories for notification targets, associations, and deliveries."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from storage.alert_orm import Alert
from storage.entity_records import PageResult
from storage.notification_orm import (
    DeliveryStatus,
    NotificationChannelType,
    NotificationDelivery,
    NotificationDeliveryAttempt,
    NotificationTarget,
    RuleNotificationTarget,
)
from storage.notification_records import (
    DeliveryAttemptRecord,
    DeliveryListFilter,
    NotificationDeliveryRecord,
    NotificationTargetCreate,
    NotificationTargetRecord,
    NotificationTargetUpdate,
)
from storage.sqlalchemy_db import session_scope

T = TypeVar("T")


class NotificationConflictError(Exception):
    """Duplicate name, association, or invalid state transition."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class NotificationTargetRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        data: NotificationTargetCreate,
        *,
        signing_secret_encrypted: str | None = None,
        session: Session | None = None,
    ) -> NotificationTargetRecord:
        def _write(active: Session) -> NotificationTargetRecord:
            row = NotificationTarget(
                id=uuid.uuid4(),
                name=data.name,
                channel_type=data.channel_type,
                url=data.url,
                enabled=data.enabled,
                is_global=data.is_global,
                signing_secret_encrypted=signing_secret_encrypted,
                severity_filters=list(data.severity_filters),
                extra=dict(data.metadata),
            )
            active.add(row)
            try:
                active.flush()
            except IntegrityError as error:
                raise NotificationConflictError(
                    f"Notification target name already exists: {data.name!r}"
                ) from error
            return self._to_target(row)

        return self._with_session(session, _write)

    def get_by_id(
        self, target_id: UUID, *, session: Session | None = None
    ) -> NotificationTargetRecord | None:
        def _read(active: Session) -> NotificationTargetRecord | None:
            row = active.get(NotificationTarget, target_id)
            return self._to_target(row) if row else None

        return self._with_session(session, _read)

    def get_row_by_id(
        self, target_id: UUID, *, session: Session | None = None
    ) -> NotificationTarget | None:
        """Return ORM row including encrypted secret (worker use)."""

        def _read(active: Session) -> NotificationTarget | None:
            return active.get(NotificationTarget, target_id)

        return self._with_session(session, _read)

    def update(
        self,
        target_id: UUID,
        data: NotificationTargetUpdate,
        *,
        signing_secret_encrypted: str | None = None,
        set_signing_secret: bool = False,
        session: Session | None = None,
    ) -> NotificationTargetRecord:
        def _write(active: Session) -> NotificationTargetRecord:
            row = active.get(NotificationTarget, target_id)
            if row is None:
                raise LookupError(f"Notification target not found: {target_id}")
            if data.name is not None:
                row.name = data.name
            if data.url is not None:
                row.url = data.url
            if data.enabled is not None:
                row.enabled = data.enabled
            if data.is_global is not None:
                row.is_global = data.is_global
            if data.severity_filters is not None:
                row.severity_filters = list(data.severity_filters)
            if data.metadata is not None:
                row.extra = dict(data.metadata)
            if data.clear_signing_secret:
                row.signing_secret_encrypted = None
            elif set_signing_secret:
                row.signing_secret_encrypted = signing_secret_encrypted
            try:
                active.flush()
            except IntegrityError as error:
                raise NotificationConflictError(
                    "Notification target name conflict"
                ) from error
            return self._to_target(row)

        return self._with_session(session, _write)

    def list_targets(
        self,
        *,
        enabled: bool | None = None,
        is_global: bool | None = None,
        limit: int = 50,
        offset: int = 0,
        session: Session | None = None,
    ) -> PageResult:
        def _read(active: Session) -> PageResult:
            count_q = select(func.count()).select_from(NotificationTarget)
            list_q = select(NotificationTarget).order_by(
                NotificationTarget.name.asc()
            )
            if enabled is not None:
                count_q = count_q.where(NotificationTarget.enabled.is_(enabled))
                list_q = list_q.where(NotificationTarget.enabled.is_(enabled))
            if is_global is not None:
                count_q = count_q.where(
                    NotificationTarget.is_global.is_(is_global)
                )
                list_q = list_q.where(
                    NotificationTarget.is_global.is_(is_global)
                )
            total = int(active.scalar(count_q) or 0)
            rows = active.scalars(list_q.offset(offset).limit(limit)).all()
            return PageResult(
                items=[self._to_target(r) for r in rows],
                total=total,
                limit=limit,
                offset=offset,
            )

        return self._with_session(session, _read)

    def list_matching_for_alert(
        self,
        *,
        rule_id: UUID,
        severity: str,
        session: Session | None = None,
    ) -> list[NotificationTargetRecord]:
        """Global enabled targets matching severity + rule associations.

        De-duplicates by target id. Global targets match when severity_filters
        is empty or contains the alert severity (case-insensitive).
        """

        def _read(active: Session) -> list[NotificationTargetRecord]:
            severity_l = severity.lower()
            seen: dict[UUID, NotificationTargetRecord] = {}

            global_rows = active.scalars(
                select(NotificationTarget).where(
                    NotificationTarget.enabled.is_(True),
                    NotificationTarget.is_global.is_(True),
                )
            ).all()
            for row in global_rows:
                filters = [str(s).lower() for s in (row.severity_filters or [])]
                if filters and severity_l not in filters:
                    continue
                seen[row.id] = self._to_target(row)

            assoc_stmt = (
                select(NotificationTarget)
                .join(
                    RuleNotificationTarget,
                    RuleNotificationTarget.target_id == NotificationTarget.id,
                )
                .where(
                    RuleNotificationTarget.rule_id == rule_id,
                    RuleNotificationTarget.enabled.is_(True),
                    NotificationTarget.enabled.is_(True),
                )
            )
            for row in active.scalars(assoc_stmt).all():
                filters = [str(s).lower() for s in (row.severity_filters or [])]
                if filters and severity_l not in filters:
                    continue
                seen[row.id] = self._to_target(row)

            return list(seen.values())

        return self._with_session(session, _read)

    def _to_target(self, row: NotificationTarget) -> NotificationTargetRecord:
        return NotificationTargetRecord(
            id=row.id,
            name=row.name,
            channel_type=row.channel_type,
            url=row.url,
            enabled=bool(row.enabled),
            is_global=bool(row.is_global),
            has_signing_secret=bool(row.signing_secret_encrypted),
            severity_filters=list(row.severity_filters or []),
            metadata=dict(row.extra or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)


class RuleNotificationTargetRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def associate(
        self,
        rule_id: UUID,
        target_id: UUID,
        *,
        enabled: bool = True,
        session: Session | None = None,
    ) -> None:
        def _write(active: Session) -> None:
            existing = active.scalars(
                select(RuleNotificationTarget).where(
                    RuleNotificationTarget.rule_id == rule_id,
                    RuleNotificationTarget.target_id == target_id,
                )
            ).first()
            if existing is not None:
                existing.enabled = enabled
                active.flush()
                return
            active.add(
                RuleNotificationTarget(
                    id=uuid.uuid4(),
                    rule_id=rule_id,
                    target_id=target_id,
                    enabled=enabled,
                )
            )
            try:
                active.flush()
            except IntegrityError as error:
                raise NotificationConflictError(
                    "Rule already associated with this target"
                ) from error

        return self._with_session(session, _write)

    def disassociate(
        self,
        rule_id: UUID,
        target_id: UUID,
        *,
        session: Session | None = None,
    ) -> bool:
        def _write(active: Session) -> bool:
            row = active.scalars(
                select(RuleNotificationTarget).where(
                    RuleNotificationTarget.rule_id == rule_id,
                    RuleNotificationTarget.target_id == target_id,
                )
            ).first()
            if row is None:
                return False
            active.delete(row)
            active.flush()
            return True

        return self._with_session(session, _write)

    def list_for_rule(
        self, rule_id: UUID, *, session: Session | None = None
    ) -> list[NotificationTargetRecord]:
        def _read(active: Session) -> list[NotificationTargetRecord]:
            stmt = (
                select(NotificationTarget, RuleNotificationTarget.enabled)
                .join(
                    RuleNotificationTarget,
                    RuleNotificationTarget.target_id == NotificationTarget.id,
                )
                .where(RuleNotificationTarget.rule_id == rule_id)
                .order_by(NotificationTarget.name.asc())
            )
            mapper = NotificationTargetRepository(self._session_factory)
            results: list[NotificationTargetRecord] = []
            for target, assoc_enabled in active.execute(stmt).all():
                rec = mapper._to_target(target)
                # Surface association enabled via metadata for API convenience
                meta = dict(rec.metadata)
                meta["_association_enabled"] = bool(assoc_enabled)
                results.append(
                    NotificationTargetRecord(
                        id=rec.id,
                        name=rec.name,
                        channel_type=rec.channel_type,
                        url=rec.url,
                        enabled=rec.enabled and bool(assoc_enabled),
                        is_global=rec.is_global,
                        has_signing_secret=rec.has_signing_secret,
                        severity_filters=list(rec.severity_filters),
                        metadata=meta,
                        created_at=rec.created_at,
                        updated_at=rec.updated_at,
                    )
                )
            return results

        return self._with_session(session, _read)

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)


class NotificationDeliveryRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_if_absent(
        self,
        *,
        alert_id: UUID,
        target_id: UUID,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        next_attempt_at: datetime | None = None,
        delivery_id: UUID | None = None,
        session: Session | None = None,
    ) -> NotificationDeliveryRecord | None:
        """Insert delivery or return None if idempotency key already exists.

        Unique-key races are absorbed (return None) without invalidating the
        outer transaction (savepoint). Any other database error propagates so
        callers can roll back the alert+outbox transaction.
        """

        def _write(active: Session) -> NotificationDeliveryRecord | None:
            existing = active.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.idempotency_key == idempotency_key
                )
            ).first()
            if existing is not None:
                return None
            now = datetime.now(timezone.utc)
            row = NotificationDelivery(
                id=delivery_id or uuid.uuid4(),
                alert_id=alert_id,
                target_id=target_id,
                event_type=event_type,
                idempotency_key=idempotency_key,
                payload=dict(payload),
                status=DeliveryStatus.PENDING,
                attempts=0,
                next_attempt_at=next_attempt_at or now,
            )
            try:
                # Savepoint keeps outer alert transaction valid on unique races.
                with active.begin_nested():
                    active.add(row)
                    active.flush()
            except IntegrityError:
                # Only idempotency uniqueness is non-fatal.
                raced = active.scalars(
                    select(NotificationDelivery).where(
                        NotificationDelivery.idempotency_key == idempotency_key
                    )
                ).first()
                if raced is not None:
                    return None
                raise
            return self._to_delivery(row)

        return self._with_session(session, _write)

    def get_by_id(
        self, delivery_id: UUID, *, session: Session | None = None
    ) -> NotificationDeliveryRecord | None:
        def _read(active: Session) -> NotificationDeliveryRecord | None:
            row = active.get(NotificationDelivery, delivery_id)
            return self._to_delivery(row, active) if row else None

        return self._with_session(session, _read)

    def list_deliveries(
        self,
        filters: DeliveryListFilter,
        *,
        session: Session | None = None,
    ) -> PageResult:
        def _read(active: Session) -> PageResult:
            count_q = select(func.count()).select_from(NotificationDelivery)
            list_q = select(NotificationDelivery)
            conditions = []
            if filters.status is not None:
                conditions.append(NotificationDelivery.status == filters.status)
            if filters.alert_id is not None:
                conditions.append(
                    NotificationDelivery.alert_id == filters.alert_id
                )
            if filters.target_id is not None:
                conditions.append(
                    NotificationDelivery.target_id == filters.target_id
                )
            if filters.rule_id is not None:
                conditions.append(
                    NotificationDelivery.alert_id.in_(
                        select(Alert.id).where(Alert.rule_id == filters.rule_id)
                    )
                )
            if filters.created_after is not None:
                conditions.append(
                    NotificationDelivery.created_at
                    >= _aware(filters.created_after)
                )
            if filters.created_before is not None:
                conditions.append(
                    NotificationDelivery.created_at
                    <= _aware(filters.created_before)
                )
            if conditions:
                count_q = count_q.where(and_(*conditions))
                list_q = list_q.where(and_(*conditions))
            total = int(active.scalar(count_q) or 0)
            if filters.sort == "asc":
                list_q = list_q.order_by(NotificationDelivery.created_at.asc())
            else:
                list_q = list_q.order_by(NotificationDelivery.created_at.desc())
            rows = active.scalars(
                list_q.offset(filters.offset).limit(filters.limit)
            ).all()
            return PageResult(
                items=[self._to_delivery(r, active) for r in rows],
                total=total,
                limit=filters.limit,
                offset=filters.offset,
            )

        return self._with_session(session, _read)

    def claim_due(
        self,
        *,
        worker_id: str,
        batch_size: int,
        now: datetime | None = None,
        session: Session | None = None,
    ) -> list[NotificationDeliveryRecord]:
        """Atomically claim due pending/failed deliveries for processing.

        PostgreSQL uses FOR UPDATE SKIP LOCKED. SQLite claims without row
        locks (single-writer tests) by filtering unlocked rows and updating.
        """

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        def _write(active: Session) -> list[NotificationDeliveryRecord]:
            dialect = active.bind.dialect.name if active.bind else "sqlite"
            claimable = [
                DeliveryStatus.PENDING,
                DeliveryStatus.FAILED,
            ]
            base = (
                select(NotificationDelivery)
                .where(NotificationDelivery.status.in_(claimable))
                .where(NotificationDelivery.next_attempt_at <= current)
                .order_by(NotificationDelivery.next_attempt_at.asc())
                .limit(batch_size)
            )
            if dialect == "postgresql":
                stmt = base.with_for_update(skip_locked=True)
                rows = list(active.scalars(stmt).all())
            else:
                rows = list(active.scalars(base).all())

            claimed: list[NotificationDeliveryRecord] = []
            for row in rows:
                row.status = DeliveryStatus.PROCESSING
                row.locked_at = current
                row.locked_by = worker_id
            if rows:
                active.flush()
            for row in rows:
                claimed.append(self._to_delivery(row, active))
            return claimed

        return self._with_session(session, _write)

    def recover_stale_locks(
        self,
        *,
        lock_timeout_seconds: float,
        now: datetime | None = None,
        session: Session | None = None,
    ) -> int:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(seconds=lock_timeout_seconds)

        def _write(active: Session) -> int:
            stmt = (
                update(NotificationDelivery)
                .where(NotificationDelivery.status == DeliveryStatus.PROCESSING)
                .where(
                    or_(
                        NotificationDelivery.locked_at.is_(None),
                        NotificationDelivery.locked_at < cutoff,
                    )
                )
                .values(
                    status=DeliveryStatus.PENDING,
                    locked_at=None,
                    locked_by=None,
                    next_attempt_at=current,
                )
            )
            result = active.execute(stmt)
            active.flush()
            return int(result.rowcount or 0)

        return self._with_session(session, _write)

    def record_attempt_and_update(
        self,
        delivery_id: UUID,
        *,
        attempt_number: int,
        attempted_at: datetime,
        duration_ms: float | None,
        response_status: int | None,
        response_body_truncated: str | None,
        error_type: str | None,
        error_message_sanitized: str | None,
        new_status: DeliveryStatus,
        next_attempt_at: datetime | None,
        response_summary: str | None,
        last_error: str | None,
        session: Session | None = None,
    ) -> NotificationDeliveryRecord:
        def _write(active: Session) -> NotificationDeliveryRecord:
            row = active.get(NotificationDelivery, delivery_id)
            if row is None:
                raise LookupError(f"Delivery not found: {delivery_id}")
            attempt = NotificationDeliveryAttempt(
                id=uuid.uuid4(),
                delivery_id=delivery_id,
                attempt_number=attempt_number,
                attempted_at=attempted_at,
                duration_ms=duration_ms,
                response_status=response_status,
                response_body_truncated=response_body_truncated,
                error_type=error_type,
                error_message_sanitized=error_message_sanitized,
            )
            active.add(attempt)
            row.attempts = attempt_number
            row.last_attempt_at = attempted_at
            if row.first_attempt_at is None:
                row.first_attempt_at = attempted_at
            row.status = new_status
            row.response_status = response_status
            row.response_summary = response_summary
            row.last_error = last_error
            row.locked_at = None
            row.locked_by = None
            if new_status is DeliveryStatus.DELIVERED:
                row.delivered_at = attempted_at
                row.next_attempt_at = attempted_at
            elif new_status is DeliveryStatus.EXHAUSTED:
                row.exhausted_at = attempted_at
                row.next_attempt_at = attempted_at
            elif next_attempt_at is not None:
                row.next_attempt_at = next_attempt_at
            active.flush()
            return self._to_delivery(row, active)

        return self._with_session(session, _write)

    def schedule_manual_retry(
        self,
        delivery_id: UUID,
        *,
        now: datetime | None = None,
        session: Session | None = None,
    ) -> NotificationDeliveryRecord:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        def _write(active: Session) -> NotificationDeliveryRecord:
            row = active.get(NotificationDelivery, delivery_id)
            if row is None:
                raise LookupError(f"Delivery not found: {delivery_id}")
            if row.status not in (
                DeliveryStatus.FAILED,
                DeliveryStatus.EXHAUSTED,
            ):
                raise NotificationConflictError(
                    f"Manual retry not allowed for status {row.status.value}"
                )
            row.status = DeliveryStatus.PENDING
            row.next_attempt_at = current
            row.locked_at = None
            row.locked_by = None
            row.exhausted_at = None
            row.last_error = None
            active.flush()
            return self._to_delivery(row, active)

        return self._with_session(session, _write)

    def list_attempts(
        self, delivery_id: UUID, *, session: Session | None = None
    ) -> list[DeliveryAttemptRecord]:
        def _read(active: Session) -> list[DeliveryAttemptRecord]:
            stmt = (
                select(NotificationDeliveryAttempt)
                .where(NotificationDeliveryAttempt.delivery_id == delivery_id)
                .order_by(NotificationDeliveryAttempt.attempt_number.asc())
            )
            return [
                DeliveryAttemptRecord(
                    id=r.id,
                    delivery_id=r.delivery_id,
                    attempt_number=r.attempt_number,
                    attempted_at=_aware(r.attempted_at),
                    duration_ms=r.duration_ms,
                    response_status=r.response_status,
                    response_body_truncated=r.response_body_truncated,
                    error_type=r.error_type,
                    error_message_sanitized=r.error_message_sanitized,
                )
                for r in active.scalars(stmt).all()
            ]

        return self._with_session(session, _read)

    def counts_by_status(
        self, *, session: Session | None = None
    ) -> dict[str, int]:
        def _read(active: Session) -> dict[str, int]:
            stmt = select(
                NotificationDelivery.status,
                func.count(),
            ).group_by(NotificationDelivery.status)
            result = {s.value: 0 for s in DeliveryStatus}
            for status, count in active.execute(stmt).all():
                key = status.value if hasattr(status, "value") else str(status)
                result[key] = int(count)
            return result

        return self._with_session(session, _read)

    def _to_delivery(
        self,
        row: NotificationDelivery,
        session: Session | None = None,
    ) -> NotificationDeliveryRecord:
        target_name = None
        if session is not None:
            target = session.get(NotificationTarget, row.target_id)
            if target is not None:
                target_name = target.name
        return NotificationDeliveryRecord(
            id=row.id,
            alert_id=row.alert_id,
            target_id=row.target_id,
            event_type=row.event_type,
            idempotency_key=row.idempotency_key,
            status=row.status,
            attempts=int(row.attempts),
            next_attempt_at=_aware(row.next_attempt_at),
            locked_at=_aware(row.locked_at) if row.locked_at else None,
            locked_by=row.locked_by,
            first_attempt_at=(
                _aware(row.first_attempt_at) if row.first_attempt_at else None
            ),
            last_attempt_at=(
                _aware(row.last_attempt_at) if row.last_attempt_at else None
            ),
            delivered_at=_aware(row.delivered_at) if row.delivered_at else None,
            exhausted_at=(
                _aware(row.exhausted_at) if row.exhausted_at else None
            ),
            response_status=row.response_status,
            response_summary=row.response_summary,
            last_error=row.last_error,
            payload=dict(row.payload or {}),
            target_name=target_name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)
