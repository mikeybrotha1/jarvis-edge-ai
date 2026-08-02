"""Bounded retention eligibility queries and deletes (v0.10.0 phase 4).

No unbounded DELETE statements. Callers pass explicit ID batches.
Checkpoint / recovery tables are intentionally absent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from storage.alert_orm import (
    Alert,
    AlertEvaluatorState,
    AlertStatus,
    EvaluatorStateKind,
)
from storage.entity_orm import Entity, EntityObservation, EntityStatus
from storage.notification_orm import DeliveryStatus, NotificationDelivery
from storage.sqlalchemy_db import session_scope
from storage.zone_orm import EntityZoneSession, ZoneSessionStatus

T = TypeVar("T")


class RetentionRepository:
    """Domain-scoped eligible-ID fetch / count / delete for retention."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _with_session(
        self, session: Session | None, operation: Callable[[Session], T]
    ) -> T:
        if session is not None:
            return operation(session)
        with session_scope(self._session_factory) as owned:
            return operation(owned)

    # ------------------------------------------------------------------
    # observations
    # ------------------------------------------------------------------

    def count_eligible_observations(
        self, *, cutoff: datetime, session: Session | None = None
    ) -> int:
        def _read(active: Session) -> int:
            stmt = (
                select(func.count())
                .select_from(EntityObservation)
                .where(EntityObservation.observed_at < cutoff)
            )
            return int(active.scalar(stmt) or 0)

        return self._with_session(session, _read)

    def fetch_eligible_observation_ids(
        self,
        *,
        cutoff: datetime,
        limit: int,
        session: Session | None = None,
    ) -> list[UUID]:
        def _read(active: Session) -> list[UUID]:
            stmt = (
                select(EntityObservation.id)
                .where(EntityObservation.observed_at < cutoff)
                .order_by(
                    EntityObservation.observed_at.asc(),
                    EntityObservation.id.asc(),
                )
                .limit(limit)
            )
            return list(active.scalars(stmt).all())

        return self._with_session(session, _read)

    def delete_observations_by_ids(
        self, ids: Sequence[UUID], *, session: Session | None = None
    ) -> int:
        if not ids:
            return 0

        def _write(active: Session) -> int:
            result = active.execute(
                delete(EntityObservation).where(EntityObservation.id.in_(list(ids)))
            )
            active.flush()
            return int(result.rowcount or 0)

        return self._with_session(session, _write)

    # ------------------------------------------------------------------
    # entities (experimental: closed only; cascade-safe dependents)
    # ------------------------------------------------------------------
    #
    # ON DELETE CASCADE from entities.id:
    #   entity_observations, entity_snapshots, entity_zone_sessions,
    #   alerts, alert_evaluator_state
    # (notification_deliveries cascade via alerts.id)
    #
    # To avoid erasing alert/evaluator audit history and active recovery
    # state, eligibility requires zero remaining alerts and evaluator rows.
    # Prefer pruning those domains first. Domain defaults stay disabled.

    def _entity_cascade_blockers(
        self, active: Session, entity_ids: Sequence[UUID] | None = None
    ) -> set[UUID]:
        """Entity ids that must not be deleted due to dependent rows.

        Blocks when any of:
        - open zone sessions (dwell/occupancy recovery)
        - any alert row (CASCADE would wipe open/acked/resolved audit)
        - any evaluator state (CASCADE would wipe pending/triggered)
        """

        blocked: set[UUID] = set()
        open_q = select(EntityZoneSession.entity_id).where(
            EntityZoneSession.status == ZoneSessionStatus.OPEN
        )
        alert_q = select(Alert.entity_id)
        eval_q = select(AlertEvaluatorState.entity_id)
        if entity_ids is not None:
            id_list = list(entity_ids)
            if not id_list:
                return blocked
            open_q = open_q.where(EntityZoneSession.entity_id.in_(id_list))
            alert_q = alert_q.where(Alert.entity_id.in_(id_list))
            eval_q = eval_q.where(AlertEvaluatorState.entity_id.in_(id_list))
        blocked.update(active.scalars(open_q.distinct()).all())
        blocked.update(active.scalars(alert_q.distinct()).all())
        blocked.update(active.scalars(eval_q.distinct()).all())
        return blocked

    def count_eligible_entities(
        self, *, cutoff: datetime, session: Session | None = None
    ) -> int:
        def _read(active: Session) -> int:
            blocked = self._entity_cascade_blockers(active)
            stmt = (
                select(func.count())
                .select_from(Entity)
                .where(Entity.status == EntityStatus.CLOSED)
                .where(Entity.last_seen < cutoff)
            )
            if blocked:
                stmt = stmt.where(Entity.id.not_in(list(blocked)))
            return int(active.scalar(stmt) or 0)

        return self._with_session(session, _read)

    def fetch_eligible_entity_ids(
        self,
        *,
        cutoff: datetime,
        limit: int,
        session: Session | None = None,
    ) -> list[UUID]:
        def _read(active: Session) -> list[UUID]:
            blocked = self._entity_cascade_blockers(active)
            stmt = (
                select(Entity.id)
                .where(Entity.status == EntityStatus.CLOSED)
                .where(Entity.last_seen < cutoff)
            )
            if blocked:
                stmt = stmt.where(Entity.id.not_in(list(blocked)))
            stmt = stmt.order_by(Entity.last_seen.asc(), Entity.id.asc()).limit(
                limit
            )
            return list(active.scalars(stmt).all())

        return self._with_session(session, _read)

    def delete_entities_by_ids(
        self, ids: Sequence[UUID], *, session: Session | None = None
    ) -> int:
        """Delete closed cascade-safe entities; re-check safety in-txn."""

        if not ids:
            return 0

        def _write(active: Session) -> int:
            id_list = list(ids)
            blocked = self._entity_cascade_blockers(active, id_list)
            stmt = (
                select(Entity.id)
                .where(Entity.id.in_(id_list))
                .where(Entity.status == EntityStatus.CLOSED)
            )
            if blocked:
                stmt = stmt.where(Entity.id.not_in(list(blocked)))
            safe_ids = list(active.scalars(stmt).all())
            if not safe_ids:
                return 0
            result = active.execute(
                delete(Entity).where(Entity.id.in_(safe_ids))
            )
            active.flush()
            return int(result.rowcount or 0)

        return self._with_session(session, _write)

    # ------------------------------------------------------------------
    # zone sessions (closed only)
    # ------------------------------------------------------------------

    def count_eligible_zone_sessions(
        self, *, cutoff: datetime, session: Session | None = None
    ) -> int:
        def _read(active: Session) -> int:
            stmt = (
                select(func.count())
                .select_from(EntityZoneSession)
                .where(EntityZoneSession.status == ZoneSessionStatus.CLOSED)
                .where(EntityZoneSession.exited_at.is_not(None))
                .where(EntityZoneSession.exited_at < cutoff)
            )
            return int(active.scalar(stmt) or 0)

        return self._with_session(session, _read)

    def fetch_eligible_zone_session_ids(
        self,
        *,
        cutoff: datetime,
        limit: int,
        session: Session | None = None,
    ) -> list[UUID]:
        def _read(active: Session) -> list[UUID]:
            stmt = (
                select(EntityZoneSession.id)
                .where(EntityZoneSession.status == ZoneSessionStatus.CLOSED)
                .where(EntityZoneSession.exited_at.is_not(None))
                .where(EntityZoneSession.exited_at < cutoff)
                .order_by(
                    EntityZoneSession.exited_at.asc(),
                    EntityZoneSession.id.asc(),
                )
                .limit(limit)
            )
            return list(active.scalars(stmt).all())

        return self._with_session(session, _read)

    def delete_zone_sessions_by_ids(
        self, ids: Sequence[UUID], *, session: Session | None = None
    ) -> int:
        if not ids:
            return 0

        def _write(active: Session) -> int:
            result = active.execute(
                delete(EntityZoneSession)
                .where(EntityZoneSession.id.in_(list(ids)))
                .where(EntityZoneSession.status == ZoneSessionStatus.CLOSED)
            )
            active.flush()
            return int(result.rowcount or 0)

        return self._with_session(session, _write)

    # ------------------------------------------------------------------
    # alerts (resolved only)
    # ------------------------------------------------------------------

    def count_eligible_alerts(
        self, *, cutoff: datetime, session: Session | None = None
    ) -> int:
        def _read(active: Session) -> int:
            stmt = (
                select(func.count())
                .select_from(Alert)
                .where(Alert.status == AlertStatus.RESOLVED)
                .where(Alert.resolved_at.is_not(None))
                .where(Alert.resolved_at < cutoff)
            )
            return int(active.scalar(stmt) or 0)

        return self._with_session(session, _read)

    def fetch_eligible_alert_ids(
        self,
        *,
        cutoff: datetime,
        limit: int,
        session: Session | None = None,
    ) -> list[UUID]:
        def _read(active: Session) -> list[UUID]:
            stmt = (
                select(Alert.id)
                .where(Alert.status == AlertStatus.RESOLVED)
                .where(Alert.resolved_at.is_not(None))
                .where(Alert.resolved_at < cutoff)
                .order_by(Alert.resolved_at.asc(), Alert.id.asc())
                .limit(limit)
            )
            return list(active.scalars(stmt).all())

        return self._with_session(session, _read)

    def delete_alerts_by_ids(
        self, ids: Sequence[UUID], *, session: Session | None = None
    ) -> int:
        if not ids:
            return 0

        def _write(active: Session) -> int:
            result = active.execute(
                delete(Alert)
                .where(Alert.id.in_(list(ids)))
                .where(Alert.status == AlertStatus.RESOLVED)
            )
            active.flush()
            return int(result.rowcount or 0)

        return self._with_session(session, _write)

    # ------------------------------------------------------------------
    # evaluator state (cleared only; never pending/triggered)
    # ------------------------------------------------------------------

    def count_eligible_evaluator_states(
        self, *, cutoff: datetime, session: Session | None = None
    ) -> int:
        def _read(active: Session) -> int:
            stmt = (
                select(func.count())
                .select_from(AlertEvaluatorState)
                .where(AlertEvaluatorState.state == EvaluatorStateKind.CLEARED)
                .where(AlertEvaluatorState.updated_at < cutoff)
            )
            return int(active.scalar(stmt) or 0)

        return self._with_session(session, _read)

    def fetch_eligible_evaluator_state_ids(
        self,
        *,
        cutoff: datetime,
        limit: int,
        session: Session | None = None,
    ) -> list[UUID]:
        def _read(active: Session) -> list[UUID]:
            stmt = (
                select(AlertEvaluatorState.id)
                .where(AlertEvaluatorState.state == EvaluatorStateKind.CLEARED)
                .where(AlertEvaluatorState.updated_at < cutoff)
                .order_by(
                    AlertEvaluatorState.updated_at.asc(),
                    AlertEvaluatorState.id.asc(),
                )
                .limit(limit)
            )
            return list(active.scalars(stmt).all())

        return self._with_session(session, _read)

    def delete_evaluator_states_by_ids(
        self, ids: Sequence[UUID], *, session: Session | None = None
    ) -> int:
        if not ids:
            return 0

        def _write(active: Session) -> int:
            result = active.execute(
                delete(AlertEvaluatorState)
                .where(AlertEvaluatorState.id.in_(list(ids)))
                .where(
                    AlertEvaluatorState.state == EvaluatorStateKind.CLEARED
                )
            )
            active.flush()
            return int(result.rowcount or 0)

        return self._with_session(session, _write)

    # ------------------------------------------------------------------
    # notification deliveries (terminal only; attempts CASCADE)
    # ------------------------------------------------------------------

    def count_eligible_notification_deliveries(
        self, *, cutoff: datetime, session: Session | None = None
    ) -> int:
        def _read(active: Session) -> int:
            stmt = (
                select(func.count())
                .select_from(NotificationDelivery)
                .where(
                    NotificationDelivery.status.in_(
                        [DeliveryStatus.DELIVERED, DeliveryStatus.EXHAUSTED]
                    )
                )
                .where(NotificationDelivery.updated_at < cutoff)
            )
            return int(active.scalar(stmt) or 0)

        return self._with_session(session, _read)

    def fetch_eligible_notification_delivery_ids(
        self,
        *,
        cutoff: datetime,
        limit: int,
        session: Session | None = None,
    ) -> list[UUID]:
        def _read(active: Session) -> list[UUID]:
            stmt = (
                select(NotificationDelivery.id)
                .where(
                    NotificationDelivery.status.in_(
                        [DeliveryStatus.DELIVERED, DeliveryStatus.EXHAUSTED]
                    )
                )
                .where(NotificationDelivery.updated_at < cutoff)
                .order_by(
                    NotificationDelivery.updated_at.asc(),
                    NotificationDelivery.id.asc(),
                )
                .limit(limit)
            )
            return list(active.scalars(stmt).all())

        return self._with_session(session, _read)

    def delete_notification_deliveries_by_ids(
        self, ids: Sequence[UUID], *, session: Session | None = None
    ) -> int:
        if not ids:
            return 0

        def _write(active: Session) -> int:
            result = active.execute(
                delete(NotificationDelivery)
                .where(NotificationDelivery.id.in_(list(ids)))
                .where(
                    NotificationDelivery.status.in_(
                        [DeliveryStatus.DELIVERED, DeliveryStatus.EXHAUSTED]
                    )
                )
            )
            active.flush()
            return int(result.rowcount or 0)

        return self._with_session(session, _write)
