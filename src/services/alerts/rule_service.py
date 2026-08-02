"""Alert rule and alert query/mutation services."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from services.alerts.rule_validation import (
    RuleValidationError,
    validate_rule_create,
    validate_rule_update,
)
from storage.activity_notify import ActivityNotificationPublisher
from storage.alert_orm import AlertSeverity, AlertStatus
from storage.alert_records import (
    AlertListFilter,
    AlertRecord,
    AlertRuleRecord,
)
from storage.alert_repositories import (
    AlertConflictError,
    AlertRepository,
    AlertRuleRepository,
)
from storage.entity_records import PageResult
from storage.sqlalchemy_db import session_scope
from sqlalchemy.orm import Session, sessionmaker


class AlertNotFoundError(LookupError):
    pass


class AlertRuleService:
    def __init__(
        self,
        rule_repository: AlertRuleRepository,
        *,
        max_rules: int = 100,
        max_metadata_bytes: int = 8192,
        default_cooldown: int = 60,
        logger: logging.Logger | None = None,
    ) -> None:
        self._rules = rule_repository
        self.max_rules = max_rules
        self.max_metadata_bytes = max_metadata_bytes
        self.default_cooldown = default_cooldown
        self._logger = logger or logging.getLogger(__name__)

    def list_rules(
        self, *, enabled: bool | None = None, limit: int = 50, offset: int = 0
    ) -> PageResult:
        return self._rules.list_rules(
            enabled=enabled, limit=limit, offset=offset
        )

    def get_rule(self, rule_id: UUID) -> AlertRuleRecord:
        record = self._rules.get_by_id(rule_id)
        if record is None:
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")
        return record

    def create_rule(self, data: dict[str, Any]) -> AlertRuleRecord:
        create = validate_rule_create(
            data,
            max_metadata_bytes=self.max_metadata_bytes,
            default_cooldown=self.default_cooldown,
        )
        if self._rules.count() >= self.max_rules:
            raise RuleValidationError(
                f"maximum alert rules is {self.max_rules}."
            )
        try:
            return self._rules.create(create)
        except AlertConflictError as error:
            raise error

    def update_rule(self, rule_id: UUID, data: dict[str, Any]) -> AlertRuleRecord:
        current = self.get_rule(rule_id)
        update = validate_rule_update(
            data,
            current_type=current.rule_type,
            max_metadata_bytes=self.max_metadata_bytes,
        )
        try:
            return self._rules.update(rule_id, update)
        except LookupError as error:
            raise AlertNotFoundError(str(error)) from error
        except AlertConflictError:
            raise


class AlertQueryService:
    def __init__(
        self,
        alert_repository: AlertRepository,
        *,
        session_factory: sessionmaker[Session] | None = None,
        activity_publisher: ActivityNotificationPublisher | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._alerts = alert_repository
        self._session_factory = session_factory
        self._publisher = activity_publisher
        self._logger = logger or logging.getLogger(__name__)

    def list_alerts(self, **kwargs: Any) -> PageResult:
        status = kwargs.get("status")
        severity = kwargs.get("severity")
        status_enum = AlertStatus(status) if status else None
        severity_enum = AlertSeverity(severity) if severity else None
        return self._alerts.list_alerts(
            AlertListFilter(
                status=status_enum,
                rule_id=kwargs.get("rule_id"),
                severity=severity_enum,
                entity_id=kwargs.get("entity_id"),
                zone_id=kwargs.get("zone_id"),
                camera_id=kwargs.get("camera_id"),
                triggered_after=kwargs.get("triggered_after"),
                triggered_before=kwargs.get("triggered_before"),
                limit=kwargs.get("limit") or 50,
                offset=kwargs.get("offset") or 0,
                sort=kwargs.get("sort") or "desc",
            )
        )

    def get_alert(self, alert_id: UUID) -> AlertRecord:
        record = self._alerts.get_by_id(alert_id)
        if record is None:
            raise AlertNotFoundError(f"Alert not found: {alert_id}")
        return record

    def count_active(self) -> int:
        return self._alerts.count_open()

    def acknowledge(self, alert_id: UUID) -> AlertRecord:
        now = datetime.now(timezone.utc)
        try:
            return self._alerts.acknowledge(alert_id, at=now)
        except LookupError as error:
            raise AlertNotFoundError(str(error)) from error

    def resolve(self, alert_id: UUID) -> AlertRecord:
        now = datetime.now(timezone.utc)
        if self._session_factory is None or self._publisher is None:
            try:
                return self._alerts.resolve(alert_id, at=now)
            except LookupError as error:
                raise AlertNotFoundError(str(error)) from error

        try:
            with session_scope(self._session_factory) as session:
                alert = self._alerts.resolve(alert_id, at=now, session=session)
                if alert.resolved_at is not None:
                    self._publisher.publish_spatial_event(
                        session,
                        event_id=f"alert-resolved:{alert.id}",
                        event_type="alert_resolved",
                        occurred_at=alert.resolved_at,
                    )
                return alert
        except LookupError as error:
            raise AlertNotFoundError(str(error)) from error
