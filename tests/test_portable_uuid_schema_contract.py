"""Schema contract: PortableUUID must match VARCHAR(36) migrations.

Prevents reintroduction of native PG UUID binds that break
``character varying = uuid`` on released alert/notification tables.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from storage.alert_orm import Alert, AlertEvaluatorState, AlertRule
from storage.entity_orm import Entity, PortableUUID
from storage.notification_orm import (
    NotificationDelivery,
    NotificationTarget,
    RuleNotificationTarget,
)
from storage.zone_orm import EntityZoneSession, Zone


ROOT = Path(__file__).resolve().parents[1]

# Columns that migrations define as String(36) / TEXT UUID strings.
_ALERT_NOTIFICATION_PORTABLE_UUID_ATTRS = (
    (AlertRule, "id"),
    (Alert, "id"),
    (Alert, "rule_id"),
    (Alert, "entity_id"),
    (Alert, "zone_id"),
    (AlertEvaluatorState, "id"),
    (AlertEvaluatorState, "rule_id"),
    (AlertEvaluatorState, "entity_id"),
    (AlertEvaluatorState, "zone_id"),
    (AlertEvaluatorState, "alert_id"),
    (NotificationTarget, "id"),
    (RuleNotificationTarget, "id"),
    (RuleNotificationTarget, "rule_id"),
    (RuleNotificationTarget, "target_id"),
    (NotificationDelivery, "id"),
    (NotificationDelivery, "alert_id"),
    (NotificationDelivery, "target_id"),
    (Entity, "id"),
    (Zone, "id"),
    (EntityZoneSession, "id"),
    (EntityZoneSession, "entity_id"),
    (EntityZoneSession, "zone_id"),
)


def test_portable_uuid_dialect_impl_is_varchar_36() -> None:
    col_type = PortableUUID()
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        impl = col_type.load_dialect_impl(dialect)
        assert isinstance(impl, sa.String)
        assert impl.length == 36


def test_portable_uuid_bind_always_string() -> None:
    col_type = PortableUUID()
    uid = uuid.uuid4()
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        bound = col_type.process_bind_param(uid, dialect)
        assert isinstance(bound, str)
        assert bound == str(uid)
        bound2 = col_type.process_bind_param(str(uid), dialect)
        assert bound2 == str(uid)


def test_alert_notification_orm_columns_use_portable_uuid() -> None:
    for model, attr in _ALERT_NOTIFICATION_PORTABLE_UUID_ATTRS:
        column = sa.inspect(model).columns[attr]
        assert isinstance(column.type, PortableUUID), (
            f"{model.__tablename__}.{attr} must use PortableUUID, "
            f"got {type(column.type)!r}"
        )


def test_migration_sql_uses_string_36_for_alert_identifiers() -> None:
    """Released SQL migrations store identifiers as VARCHAR(36), not UUID."""

    alert_sql = (ROOT / "migrations" / "005_durable_alerts.sql").read_text(
        encoding="utf-8"
    )
    notif_sql = (
        ROOT / "migrations" / "006_outbound_notifications.sql"
    ).read_text(encoding="utf-8")
    # Positive: VARCHAR(36) for id/rule_id/entity_id
    for fragment in (
        "id VARCHAR(36)",
        "rule_id VARCHAR(36)",
        "entity_id VARCHAR(36)",
        "zone_id VARCHAR(36)",
        "alert_id VARCHAR(36)",
        "target_id VARCHAR(36)",
    ):
        assert fragment in alert_sql or fragment in notif_sql, fragment
    # Negative: no native UUID column type in these domain migrations
    assert not re.search(
        r"\bUUID\b", alert_sql, flags=re.IGNORECASE
    ), "005_durable_alerts.sql must not declare native UUID columns"
    assert not re.search(
        r"\bUUID\b", notif_sql, flags=re.IGNORECASE
    ), "006_outbound_notifications.sql must not declare native UUID columns"


def test_alert_rule_id_predicate_compiles_without_uuid_cast() -> None:
    """WHERE alerts.rule_id = :p must not emit ::UUID against VARCHAR schema."""

    rule_id = uuid.uuid4()
    stmt = (
        select(Alert.triggered_at)
        .where(Alert.rule_id == rule_id)
        .where(Alert.subject_key == "subject")
        .limit(1)
    )
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    sql = str(compiled)
    # No cast of bind to UUID for rule_id comparison
    assert "::UUID" not in sql.upper().replace(" ", "")
    assert "rule_id" in sql

    # Bind processing through the column type
    bound = Alert.rule_id.type.process_bind_param(
        rule_id, postgresql.dialect()
    )
    assert isinstance(bound, str)
    assert bound == str(rule_id)


def test_last_trigger_and_open_lookup_accept_uuid_python_values() -> None:
    """Repository predicates accept UUID objects against string-mapped columns."""

    from storage.alert_orm import AlertRuleType, AlertSeverity
    from storage.alert_records import AlertRuleCreate
    from storage.alert_repositories import (
        AlertRepository,
        AlertRuleRepository,
    )
    from storage.entity_records import EntityCreate
    from storage.entity_repository import EntityRepository
    from storage.sqlalchemy_db import (
        create_entity_engine,
        create_entity_schema,
        create_session_factory,
    )
    from datetime import datetime, timezone

    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)

    # Prove PG dialect bind path for the same predicate shape
    dialect = postgresql.dialect()
    rule_uuid = uuid.uuid4()
    stmt = select(Alert).where(Alert.rule_id == rule_uuid)
    sql = str(stmt.compile(dialect=dialect))
    assert "::UUID" not in sql.upper().replace(" ", "")

    rules = AlertRuleRepository(factory)
    alerts = AlertRepository(factory)
    entities = EntityRepository(factory)
    now = datetime.now(timezone.utc)
    entity = entities.create(
        EntityCreate(
            identity_key=f"cam:{uuid.uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label="person",
            track_id=1,
            camera_id="cam",
            first_seen=now,
            last_seen=now,
            confidence=0.9,
        )
    )
    rule = rules.create(
        AlertRuleCreate(
            name=f"contract-{uuid.uuid4().hex[:8]}",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
    )
    # rule.id is uuid.UUID in Python; last_trigger must accept it
    assert isinstance(rule.id, uuid.UUID)
    assert alerts.last_trigger_for_subject(rule.id, f"e:{entity.id}") is None

    created = alerts.create(
        rule_id=rule.id,
        severity=AlertSeverity.WARNING,
        entity_id=entity.id,
        zone_id=None,
        camera_id="cam",
        source_event_id="src",
        subject_key=f"e:{entity.id}",
        idempotency_key=f"idem-{uuid.uuid4().hex}",
        triggered_at=now,
        summary="contract",
        payload={},
    )
    last = alerts.last_trigger_for_subject(rule.id, f"e:{entity.id}")
    assert last is not None
    open_row = alerts.get_open_for_subject(rule.id, f"e:{entity.id}")
    assert open_row is not None
    assert open_row.id == created.id
    engine.dispose()
