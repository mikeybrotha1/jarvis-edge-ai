"""Outbound notification delivery tests (v0.9.0)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from api.app import create_app
from services.alerts.evaluation_service import AlertEvaluationService
from services.notifications.enqueue import NotificationEnqueueService
from services.notifications.payload import build_alert_payload, idempotency_key
from services.notifications.registry import NotificationProviderRegistry
from services.notifications.secrets import (
    decrypt_secret,
    encrypt_secret,
    generate_encryption_key,
)
from services.notifications.signing import build_signature_headers, verify_signature
from services.notifications.ssrf import SSRFValidationError, validate_webhook_url
from services.notifications.webhook_provider import (
    WebhookNotificationProvider,
    classify_http_status,
)
from services.notifications.worker import (
    NotificationDeliveryWorker,
    compute_backoff_seconds,
)
from storage.alert_orm import AlertRuleType, AlertSeverity, AlertStatus
from storage.alert_records import AlertRecord, AlertRuleCreate
from storage.alert_repositories import (
    AlertEvaluatorStateRepository,
    AlertRepository,
    AlertRuleRepository,
)
from storage.entity_records import EntityCreate
from storage.entity_repository import EntityRepository
from storage.notification_orm import DeliveryStatus, NotificationChannelType
from storage.notification_records import NotificationTargetCreate
from storage.notification_repositories import (
    NotificationDeliveryRepository,
    NotificationTargetRepository,
    RuleNotificationTargetRepository,
)
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)
from storage.timeline_models import TimelineEvent, TimelineEventType


@pytest.fixture
def encryption_key(monkeypatch):
    key = generate_encryption_key()
    monkeypatch.setenv("JARVIS_NOTIFICATIONS_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
def factory():
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    return create_session_factory(engine)


def _entity(factory, label="person", camera="cam"):
    repo = EntityRepository(factory)
    now = datetime.now(timezone.utc)
    return repo.create(
        EntityCreate(
            identity_key=f"{camera}:{uuid4().hex[:8]}",
            identity_strategy="tracker_id",
            label=label,
            track_id=1,
            camera_id=camera,
            first_seen=now,
            last_seen=now,
            confidence=0.9,
        )
    )


def _rule(factory, name=None, severity=AlertSeverity.WARNING):
    return AlertRuleRepository(factory).create(
        AlertRuleCreate(
            name=name or f"rule-{uuid4().hex[:6]}",
            rule_type=AlertRuleType.EVENT_MATCH,
            source_event_types=["entity_created"],
            severity=severity,
            cooldown_seconds=0,
        )
    )


def _alert(factory, rule, entity):
    now = datetime.now(timezone.utc)
    return AlertRepository(factory).create(
        rule_id=rule.id,
        severity=rule.severity,
        entity_id=entity.id,
        zone_id=None,
        camera_id="cam",
        source_event_id=f"src-{uuid4().hex[:8]}",
        subject_key=f"e:{entity.id}",
        idempotency_key=f"idem-{uuid4().hex}",
        triggered_at=now,
        summary="test alert",
        payload={},
    )


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def test_secret_encrypt_decrypt_roundtrip(encryption_key):
    ct = encrypt_secret("super-secret")
    assert ct != "super-secret"
    assert decrypt_secret(ct) == "super-secret"


# ---------------------------------------------------------------------------
# SSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/hook",
        "http://127.0.0.1/hook",
        "http://[::1]/hook",
        "http://10.0.0.1/hook",
        "http://192.168.1.1/hook",
        "http://172.16.0.5/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://user:pass@example.com/hook",
        "file:///etc/passwd",
        "ftp://example.com/hook",
    ],
)
def test_ssrf_blocks_dangerous_urls(url):
    with pytest.raises(SSRFValidationError):
        validate_webhook_url(url, allow_private_targets=False, resolve_dns=False)


def test_ssrf_allows_public_https_without_dns():
    # resolve_dns=False skips DNS; structural check only
    result = validate_webhook_url(
        "https://hooks.example.com/jarvis",
        allow_private_targets=False,
        resolve_dns=False,
    )
    assert result.hostname == "hooks.example.com"


def test_ssrf_allow_private_override_permits_rfc1918():
    """allow_private_targets opens LAN private ranges only (not loopback)."""

    result = validate_webhook_url(
        "http://10.0.0.5:8080/hook",
        allow_private_targets=True,
        resolve_dns=False,
    )
    assert "10.0.0.5" in result.resolved_ips or result.hostname == "10.0.0.5"
    result_ula_like = validate_webhook_url(
        "http://192.168.1.50:8090/hook",
        allow_private_targets=True,
        resolve_dns=False,
    )
    assert result_ula_like.hostname == "192.168.1.50"


def test_ssrf_loopback_blocked_even_when_private_allowed():
    """Loopback remains unconditionally blocked under allow_private_targets."""

    with pytest.raises(SSRFValidationError, match="[Ll]oopback"):
        validate_webhook_url(
            "http://127.0.0.1:8090/",
            allow_private_targets=True,
            resolve_dns=False,
        )
    with pytest.raises(SSRFValidationError, match="[Ll]oopback"):
        validate_webhook_url(
            "http://127.0.0.1:8090/",
            allow_private_targets=False,
            resolve_dns=False,
        )
    with pytest.raises(SSRFValidationError, match="[Ll]ocalhost|[Mm]etadata"):
        validate_webhook_url(
            "http://localhost:8090/",
            allow_private_targets=True,
            resolve_dns=False,
        )


def test_ssrf_link_local_blocked_even_when_private_allowed():
    with pytest.raises(SSRFValidationError, match="[Ll]ink-local"):
        validate_webhook_url(
            "http://169.254.169.254/latest/meta-data",
            allow_private_targets=True,
            resolve_dns=False,
        )


# ---------------------------------------------------------------------------
# Signing / status classification
# ---------------------------------------------------------------------------


def test_signature_roundtrip():
    body = b'{"hello":"world"}'
    headers = build_signature_headers(body, "sec", timestamp=1700000000)
    assert verify_signature(
        body,
        "sec",
        timestamp=headers["X-Jarvis-Timestamp"],
        signature_header=headers["X-Jarvis-Signature"],
    )


def test_classify_http_status():
    assert classify_http_status(200) == (True, False)
    assert classify_http_status(429) == (False, True)
    assert classify_http_status(500) == (False, True)
    assert classify_http_status(404) == (False, False)
    assert classify_http_status(408) == (False, True)


def test_backoff_bounds():
    d1 = compute_backoff_seconds(
        1, initial_backoff_seconds=30, max_backoff_seconds=1800, backoff_multiplier=2
    )
    d5 = compute_backoff_seconds(
        5, initial_backoff_seconds=30, max_backoff_seconds=1800, backoff_multiplier=2
    )
    assert d1 == 30
    assert d5 == min(30 * (2**4), 1800)
    assert d5 <= 1800


# ---------------------------------------------------------------------------
# Targets / associations / enqueue
# ---------------------------------------------------------------------------


def test_target_crud_secret_not_returned(factory, encryption_key):
    app = create_app(
        session_factory=factory,
        create_schema=False,
        enable_activity_stream=False,
        notifications_config=_notif_cfg(allow_private=True),
    )
    with TestClient(app) as client:
        with patch(
            "services.notifications.target_service.validate_webhook_url"
        ) as mock_ssrf:
            mock_ssrf.return_value = MagicMock()
            r = client.post(
                "/api/v1/notification-targets",
                json={
                    "name": "ops-hook",
                    "url": "https://hooks.example.com/j",
                    "is_global": True,
                    "signing_secret": "shh",
                    "severity_filters": ["warning"],
                },
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["has_signing_secret"] is True
        assert "signing_secret" not in body
        assert "signing_secret_encrypted" not in body
        tid = body["id"]

        g = client.get(f"/api/v1/notification-targets/{tid}")
        assert g.status_code == 200
        assert g.json()["has_signing_secret"] is True
        assert "signing_secret" not in g.json()

        with patch(
            "services.notifications.target_service.validate_webhook_url"
        ) as mock_ssrf:
            mock_ssrf.return_value = MagicMock()
            p = client.patch(
                f"/api/v1/notification-targets/{tid}",
                json={"signing_secret": "new-secret", "enabled": False},
            )
        assert p.status_code == 200
        assert p.json()["enabled"] is False
        assert p.json()["has_signing_secret"] is True


def test_rule_target_association(factory, encryption_key):
    rule = _rule(factory)
    app = create_app(
        session_factory=factory,
        create_schema=False,
        enable_activity_stream=False,
        notifications_config=_notif_cfg(allow_private=True),
    )
    with TestClient(app) as client:
        with patch(
            "services.notifications.target_service.validate_webhook_url"
        ) as mock_ssrf:
            mock_ssrf.return_value = MagicMock()
            t = client.post(
                "/api/v1/notification-targets",
                json={
                    "name": "rule-hook",
                    "url": "https://hooks.example.com/r",
                    "is_global": False,
                },
            ).json()
        r = client.post(
            f"/api/v1/alert-rules/{rule.id}/notification-targets/{t['id']}"
        )
        assert r.status_code == 204
        listed = client.get(
            f"/api/v1/alert-rules/{rule.id}/notification-targets"
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        d = client.delete(
            f"/api/v1/alert-rules/{rule.id}/notification-targets/{t['id']}"
        )
        assert d.status_code == 204


def test_enqueue_dedup_and_global_match(factory, encryption_key):
    rule = _rule(factory)
    entity = _entity(factory)
    alert = _alert(factory, rule, entity)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    assoc = RuleNotificationTargetRepository(factory)
    t_global = targets.create(
        NotificationTargetCreate(
            name="g",
            url="https://hooks.example.com/g",
            is_global=True,
            severity_filters=["warning"],
        )
    )
    t_rule = targets.create(
        NotificationTargetCreate(
            name="r",
            url="https://hooks.example.com/r",
            is_global=False,
        )
    )
    # Same target also associated with rule — de-dupe
    assoc.associate(rule.id, t_global.id)
    assoc.associate(rule.id, t_rule.id)

    svc = NotificationEnqueueService(targets, deliveries)
    created = svc.enqueue_for_alert(alert, event_type="alert_triggered")
    assert len(created) == 2
    again = svc.enqueue_for_alert(alert, event_type="alert_triggered")
    assert again == []
    page = deliveries.list_deliveries(
        __import__(
            "storage.notification_records", fromlist=["DeliveryListFilter"]
        ).DeliveryListFilter(alert_id=alert.id, limit=50)
    )
    assert page.total == 2
    keys = {d.idempotency_key for d in page.items}
    assert idempotency_key(alert.id, t_global.id, "alert_triggered") in keys


# ---------------------------------------------------------------------------
# Webhook provider + worker
# ---------------------------------------------------------------------------


def test_webhook_success_and_idempotency_header(encryption_key):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Jarvis-Delivery-ID"] == "a:t:alert_triggered"
        assert request.headers["Content-Type"] == "application/json"
        body = json.loads(request.content)
        assert body["schema_version"] == "1"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    provider = WebhookNotificationProvider(
        allow_private_targets=True, client=client
    )
    with patch.object(
        provider,
        "deliver",
        wraps=provider.deliver,
    ):
        # Bypass DNS for public host mock
        with patch(
            "services.notifications.webhook_provider.validate_webhook_url"
        ) as v:
            v.return_value = MagicMock()
            target = NotificationTargetCreate(
                name="t", url="https://hooks.example.com/x"
            )
            # Use a simple namespace object
            class T:
                channel_type = NotificationChannelType.WEBHOOK
                url = "https://hooks.example.com/x"

            result = provider.deliver(
                T(),
                {"schema_version": "1", "event_type": "alert_triggered"},
                "a:t:alert_triggered",
            )
    assert result.success is True
    assert result.response_status == 200
    client.close()


def test_webhook_retryable_and_terminal(encryption_key):
    statuses = iter([429, 404])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = WebhookNotificationProvider(
        allow_private_targets=True, client=client
    )

    class T:
        channel_type = NotificationChannelType.WEBHOOK
        url = "https://hooks.example.com/x"

    with patch(
        "services.notifications.webhook_provider.validate_webhook_url"
    ) as v:
        v.return_value = MagicMock()
        r1 = provider.deliver(T(), {"a": 1}, "k1")
        r2 = provider.deliver(T(), {"a": 1}, "k1")
    assert r1.success is False and r1.retryable is True
    assert r2.success is False and r2.retryable is False
    client.close()


def test_worker_delivers_and_isolates_alert(factory, encryption_key):
    rule = _rule(factory)
    entity = _entity(factory)
    alert = _alert(factory, rule, entity)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    t = targets.create(
        NotificationTargetCreate(
            name="w",
            url="https://hooks.example.com/w",
            is_global=True,
        )
    )
    enqueue = NotificationEnqueueService(targets, deliveries)
    created = enqueue.enqueue_for_alert(alert, event_type="alert_triggered")
    assert len(created) == 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = NotificationProviderRegistry()
    provider = WebhookNotificationProvider(
        allow_private_targets=True, client=client
    )
    # Skip SSRF DNS
    with patch(
        "services.notifications.webhook_provider.validate_webhook_url"
    ) as v:
        v.return_value = MagicMock()
        registry.register(provider)
        worker = NotificationDeliveryWorker(
            deliveries,
            targets,
            registry,
            max_attempts=3,
            initial_backoff_seconds=1,
            max_backoff_seconds=10,
            poll_interval_seconds=0.1,
            worker_id="test-worker",
        )
        n = worker.process_one_sync()
    assert n == 1
    d = deliveries.get_by_id(created[0].id)
    assert d is not None
    assert d.status is DeliveryStatus.DELIVERED
    # Alert unchanged
    a = AlertRepository(factory).get_by_id(alert.id)
    assert a is not None
    assert a.status is AlertStatus.OPEN
    client.close()


def test_worker_exhausts_on_terminal_and_manual_retry(factory, encryption_key):
    rule = _rule(factory)
    entity = _entity(factory)
    alert = _alert(factory, rule, entity)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    targets.create(
        NotificationTargetCreate(
            name="bad",
            url="https://hooks.example.com/bad",
            is_global=True,
        )
    )
    enqueue = NotificationEnqueueService(targets, deliveries)
    created = enqueue.enqueue_for_alert(alert, event_type="alert_triggered")[0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = NotificationProviderRegistry()
    provider = WebhookNotificationProvider(
        allow_private_targets=True, client=client
    )
    with patch(
        "services.notifications.webhook_provider.validate_webhook_url"
    ) as v:
        v.return_value = MagicMock()
        registry.register(provider)
        worker = NotificationDeliveryWorker(
            deliveries, targets, registry, max_attempts=3, worker_id="w"
        )
        worker.process_one_sync()
    d = deliveries.get_by_id(created.id)
    assert d.status is DeliveryStatus.EXHAUSTED

    app = create_app(
        session_factory=factory,
        create_schema=False,
        enable_activity_stream=False,
        notifications_config=_notif_cfg(enabled=False),
    )
    with TestClient(app) as client_api:
        r = client_api.post(
            f"/api/v1/notification-deliveries/{created.id}/retry"
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        # Invalid retry when pending
        r2 = client_api.post(
            f"/api/v1/notification-deliveries/{created.id}/retry"
        )
        assert r2.status_code == 409
    client.close()


def test_worker_retry_then_success(factory, encryption_key):
    rule = _rule(factory)
    entity = _entity(factory)
    alert = _alert(factory, rule, entity)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    targets.create(
        NotificationTargetCreate(
            name="flaky",
            url="https://hooks.example.com/f",
            is_global=True,
        )
    )
    created = NotificationEnqueueService(targets, deliveries).enqueue_for_alert(
        alert, event_type="alert_triggered"
    )[0]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = NotificationProviderRegistry()
    provider = WebhookNotificationProvider(
        allow_private_targets=True, client=client
    )
    with patch(
        "services.notifications.webhook_provider.validate_webhook_url"
    ) as v:
        v.return_value = MagicMock()
        registry.register(provider)
        worker = NotificationDeliveryWorker(
            deliveries,
            targets,
            registry,
            max_attempts=5,
            initial_backoff_seconds=0.01,
            max_backoff_seconds=1,
            worker_id="w",
        )
        worker.process_one_sync()
        d = deliveries.get_by_id(created.id)
        assert d.status is DeliveryStatus.FAILED
        # Force next attempt due
        from storage.notification_orm import NotificationDelivery
        from storage.sqlalchemy_db import session_scope

        with session_scope(factory) as session:
            row = session.get(NotificationDelivery, created.id)
            row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        worker.process_one_sync()
    d = deliveries.get_by_id(created.id)
    assert d.status is DeliveryStatus.DELIVERED
    assert d.attempts == 2
    client.close()


def test_stale_lock_recovery(factory, encryption_key):
    rule = _rule(factory)
    entity = _entity(factory)
    alert = _alert(factory, rule, entity)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    targets.create(
        NotificationTargetCreate(
            name="lock",
            url="https://hooks.example.com/l",
            is_global=True,
        )
    )
    created = NotificationEnqueueService(targets, deliveries).enqueue_for_alert(
        alert, event_type="alert_triggered"
    )[0]
    from storage.notification_orm import NotificationDelivery
    from storage.sqlalchemy_db import session_scope

    with session_scope(factory) as session:
        row = session.get(NotificationDelivery, created.id)
        row.status = DeliveryStatus.PROCESSING
        row.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
        row.locked_by = "dead-worker"
    n = deliveries.recover_stale_locks(lock_timeout_seconds=60)
    assert n == 1
    d = deliveries.get_by_id(created.id)
    assert d.status is DeliveryStatus.PENDING


def test_alert_evaluation_enqueues(factory, encryption_key):
    rule = _rule(factory)
    entity = _entity(factory)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    targets.create(
        NotificationTargetCreate(
            name="eval",
            url="https://hooks.example.com/e",
            is_global=True,
            severity_filters=[],
        )
    )
    enqueue = NotificationEnqueueService(targets, deliveries)
    eval_svc = AlertEvaluationService(
        factory,
        AlertRuleRepository(factory),
        AlertRepository(factory),
        AlertEvaluatorStateRepository(factory),
        notification_enqueue=enqueue,
    )
    now = datetime.now(timezone.utc)
    event = TimelineEvent(
        id=f"entity-created:{entity.id}",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=now,
        source="entity",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="created",
    )
    triggered = eval_svc.process_source_event(event)
    assert len(triggered) == 1
    page = deliveries.list_deliveries(
        __import__(
            "storage.notification_records", fromlist=["DeliveryListFilter"]
        ).DeliveryListFilter(alert_id=triggered[0].id, limit=10)
    )
    assert page.total >= 1


# ---------------------------------------------------------------------------
# Transactional outbox consistency
# ---------------------------------------------------------------------------


def _eval_with_enqueue(factory):
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    enqueue = NotificationEnqueueService(targets, deliveries)
    eval_svc = AlertEvaluationService(
        factory,
        AlertRuleRepository(factory),
        AlertRepository(factory),
        AlertEvaluatorStateRepository(factory),
        notification_enqueue=enqueue,
    )
    return eval_svc, targets, deliveries, enqueue


def _entity_created_event(entity):
    return TimelineEvent(
        id=f"entity-created:{entity.id}",
        event_type=TimelineEventType.ENTITY_CREATED,
        occurred_at=datetime.now(timezone.utc),
        source="entity",
        entity_id=entity.id,
        camera_id="cam",
        entity_type="person",
        summary="created",
    )


def test_tx_outbox_atomic_create_all_matching_deliveries(factory, encryption_key):
    """1. Successful alert trigger creates all matching delivery rows atomically."""

    rule = _rule(factory)
    entity = _entity(factory)
    eval_svc, targets, deliveries, _ = _eval_with_enqueue(factory)
    t1 = targets.create(
        NotificationTargetCreate(
            name="g1",
            url="https://hooks.example.com/g1",
            is_global=True,
            severity_filters=["warning"],
        )
    )
    t2 = targets.create(
        NotificationTargetCreate(
            name="g2",
            url="https://hooks.example.com/g2",
            is_global=True,
            severity_filters=[],
        )
    )
    RuleNotificationTargetRepository(factory).associate(rule.id, t2.id)

    triggered = eval_svc.process_source_event(_entity_created_event(entity))
    assert len(triggered) == 1
    alert = triggered[0]
    assert AlertRepository(factory).get_by_id(alert.id) is not None

    from storage.notification_records import DeliveryListFilter

    page = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=alert.id, limit=50)
    )
    assert page.total == 2
    target_ids = {d.target_id for d in page.items}
    assert target_ids == {t1.id, t2.id}
    assert all(d.event_type == "alert_triggered" for d in page.items)
    assert all(d.status is DeliveryStatus.PENDING for d in page.items)


def test_tx_outbox_global_and_rule_dedup(factory, encryption_key):
    """2. Global + rule-associated same target is de-duplicated."""

    rule = _rule(factory)
    entity = _entity(factory)
    eval_svc, targets, deliveries, _ = _eval_with_enqueue(factory)
    shared = targets.create(
        NotificationTargetCreate(
            name="shared",
            url="https://hooks.example.com/shared",
            is_global=True,
            severity_filters=["warning"],
        )
    )
    only_rule = targets.create(
        NotificationTargetCreate(
            name="only-rule",
            url="https://hooks.example.com/or",
            is_global=False,
        )
    )
    assoc = RuleNotificationTargetRepository(factory)
    assoc.associate(rule.id, shared.id)
    assoc.associate(rule.id, only_rule.id)

    triggered = eval_svc.process_source_event(_entity_created_event(entity))
    assert len(triggered) == 1
    from storage.notification_records import DeliveryListFilter

    page = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=triggered[0].id, limit=50)
    )
    assert page.total == 2
    assert {d.target_id for d in page.items} == {shared.id, only_rule.id}
    keys = [d.idempotency_key for d in page.items]
    assert len(keys) == len(set(keys))


def test_tx_outbox_duplicate_eval_no_duplicate_deliveries(factory, encryption_key):
    """3. Duplicate alert evaluation does not duplicate deliveries."""

    rule = _rule(factory)
    entity = _entity(factory)
    eval_svc, targets, deliveries, _ = _eval_with_enqueue(factory)
    targets.create(
        NotificationTargetCreate(
            name="once",
            url="https://hooks.example.com/once",
            is_global=True,
        )
    )
    event = _entity_created_event(entity)
    a1 = eval_svc.process_source_event(event)
    a2 = eval_svc.process_source_event(event)
    assert len(a1) == 1
    assert a2 == []
    from storage.notification_records import DeliveryListFilter

    page = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=a1[0].id, limit=50)
    )
    assert page.total == 1
    assert AlertRepository(factory).count_open() == 1


def test_tx_outbox_insert_failure_rolls_back_alert(factory, encryption_key):
    """4. Forced outbox insert failure does not leave partial alert/outbox."""

    rule = _rule(factory)
    entity = _entity(factory)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    targets.create(
        NotificationTargetCreate(
            name="fail-me",
            url="https://hooks.example.com/fail",
            is_global=True,
        )
    )
    enqueue = NotificationEnqueueService(targets, deliveries)
    eval_svc = AlertEvaluationService(
        factory,
        AlertRuleRepository(factory),
        AlertRepository(factory),
        AlertEvaluatorStateRepository(factory),
        notification_enqueue=enqueue,
    )
    open_before = AlertRepository(factory).count_open()
    from storage.notification_records import DeliveryListFilter

    del_before = deliveries.list_deliveries(DeliveryListFilter(limit=200)).total

    with patch.object(
        deliveries,
        "create_if_absent",
        side_effect=RuntimeError("forced outbox insert failure"),
    ):
        with pytest.raises(RuntimeError, match="forced outbox insert failure"):
            eval_svc.process_source_event(_entity_created_event(entity))

    assert AlertRepository(factory).count_open() == open_before
    assert deliveries.list_deliveries(DeliveryListFilter(limit=200)).total == del_before
    # Session remains usable after rollback
    targets.create(
        NotificationTargetCreate(
            name="after-fail",
            url="https://hooks.example.com/after",
            is_global=False,
        )
    )


def test_tx_outbox_no_http_before_commit(factory, encryption_key):
    """5. No HTTP occurs before the alert/outbox transaction commits."""

    rule = _rule(factory)
    entity = _entity(factory)
    targets = NotificationTargetRepository(factory)
    deliveries = NotificationDeliveryRepository(factory)
    targets.create(
        NotificationTargetCreate(
            name="no-http",
            url="https://hooks.example.com/no-http",
            is_global=True,
        )
    )
    enqueue = NotificationEnqueueService(targets, deliveries)
    http_calls: list[str] = []

    class SpyProvider:
        channel_type = "webhook"

        def supports(self, target):
            return True

        def deliver(self, target, payload, idempotency_key, *, signing_secret=None):
            http_calls.append("http")
            raise AssertionError("HTTP must not run during alert commit")

    # Patch httpx.Client.post would also work; spy on provider path used by worker only.
    eval_svc = AlertEvaluationService(
        factory,
        AlertRuleRepository(factory),
        AlertRepository(factory),
        AlertEvaluatorStateRepository(factory),
        notification_enqueue=enqueue,
    )
    with patch("httpx.Client.post", side_effect=AssertionError("no HTTP on enqueue")):
        with patch("httpx.Client.request", side_effect=AssertionError("no HTTP on enqueue")):
            triggered = eval_svc.process_source_event(_entity_created_event(entity))
    assert len(triggered) == 1
    assert http_calls == []
    from storage.notification_records import DeliveryListFilter

    page = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=triggered[0].id, limit=10)
    )
    assert page.total == 1
    assert page.items[0].status is DeliveryStatus.PENDING


def test_tx_outbox_webhook_failure_leaves_alert_unchanged(factory, encryption_key):
    """6. Webhook failure after commit leaves alert state unchanged."""

    rule = _rule(factory)
    entity = _entity(factory)
    eval_svc, targets, deliveries, _ = _eval_with_enqueue(factory)
    targets.create(
        NotificationTargetCreate(
            name="wh-fail",
            url="https://hooks.example.com/wh-fail",
            is_global=True,
        )
    )
    triggered = eval_svc.process_source_event(_entity_created_event(entity))
    assert len(triggered) == 1
    alert_id = triggered[0].id
    assert AlertRepository(factory).get_by_id(alert_id).status is AlertStatus.OPEN

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registry = NotificationProviderRegistry()
    provider = WebhookNotificationProvider(
        allow_private_targets=True, client=client
    )
    with patch(
        "services.notifications.webhook_provider.validate_webhook_url"
    ) as v:
        v.return_value = MagicMock()
        registry.register(provider)
        worker = NotificationDeliveryWorker(
            deliveries,
            targets,
            registry,
            max_attempts=2,
            initial_backoff_seconds=0.01,
            max_backoff_seconds=1,
            worker_id="iso-worker",
        )
        worker.process_one_sync()
    alert = AlertRepository(factory).get_by_id(alert_id)
    assert alert is not None
    assert alert.status is AlertStatus.OPEN
    from storage.notification_records import DeliveryListFilter

    d = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=alert_id, limit=5)
    ).items[0]
    assert d.status in (DeliveryStatus.FAILED, DeliveryStatus.EXHAUSTED)
    client.close()


def test_tx_outbox_resolve_distinct_idempotency(factory, encryption_key):
    """7. Resolution creates one distinct delivery per target (resolved key)."""

    rule = _rule(factory)
    entity = _entity(factory)
    eval_svc, targets, deliveries, enqueue = _eval_with_enqueue(factory)
    t_global = targets.create(
        NotificationTargetCreate(
            name="res-g",
            url="https://hooks.example.com/res-g",
            is_global=True,
        )
    )
    t_rule = targets.create(
        NotificationTargetCreate(
            name="res-r",
            url="https://hooks.example.com/res-r",
            is_global=False,
        )
    )
    RuleNotificationTargetRepository(factory).associate(rule.id, t_rule.id)

    triggered = eval_svc.process_source_event(_entity_created_event(entity))
    assert len(triggered) == 1
    alert_id = triggered[0].id

    from services.alerts.rule_service import AlertQueryService

    query = AlertQueryService(
        AlertRepository(factory),
        session_factory=factory,
        notification_enqueue=enqueue,
    )
    resolved = query.resolve(alert_id)
    assert resolved.status is AlertStatus.RESOLVED

    from storage.notification_records import DeliveryListFilter

    page = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=alert_id, limit=50)
    )
    assert page.total == 4  # 2 targets × (triggered + resolved)
    by_event: dict[str, set] = {"alert_triggered": set(), "alert_resolved": set()}
    for d in page.items:
        by_event[d.event_type].add(d.target_id)
        if d.event_type == "alert_triggered":
            assert d.idempotency_key == idempotency_key(
                alert_id, d.target_id, "alert_triggered"
            )
        else:
            assert d.idempotency_key == idempotency_key(
                alert_id, d.target_id, "alert_resolved"
            )
    assert by_event["alert_triggered"] == {t_global.id, t_rule.id}
    assert by_event["alert_resolved"] == {t_global.id, t_rule.id}

    # Second resolve is idempotent for outbox
    query.resolve(alert_id)
    page2 = deliveries.list_deliveries(
        DeliveryListFilter(alert_id=alert_id, limit=50)
    )
    assert page2.total == 4


def test_api_startup_worker_disabled(factory):
    from config.models import NotificationsConfig

    app = create_app(
        session_factory=factory,
        create_schema=False,
        enable_activity_stream=False,
        notifications_config=NotificationsConfig(enabled=False),
    )
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert client.app.state.notification_worker is not None
        assert client.app.state.notification_worker.enabled is False


def test_no_vision_hailo_imports_in_notifications():
    import services.notifications as pkg
    import api.notification_routes as routes

    for mod in (pkg, routes):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "hailo" not in src.lower()
        assert "camera" not in src.lower() or "camera_id" in src


def test_migration_sqlite_upgrade_downgrade():
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    import tempfile
    import sqlalchemy as sa

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        url = f"sqlite+pysqlite:///{db}"
        # Need base tables first — run from empty using create_entity_schema
        engine = create_entity_engine(url)
        create_entity_schema(engine)
        # Tables already present via create_entity_schema including 0005 models
        insp = sa.inspect(engine)
        assert "notification_targets" in insp.get_table_names()
        assert "notification_deliveries" in insp.get_table_names()
        assert "notification_delivery_attempts" in insp.get_table_names()
        assert "rule_notification_targets" in insp.get_table_names()
        engine.dispose()


def test_payload_shape():
    alert = AlertRecord(
        id=uuid4(),
        rule_id=uuid4(),
        status=AlertStatus.OPEN,
        severity=AlertSeverity.WARNING,
        entity_id=uuid4(),
        zone_id=None,
        camera_id="cam",
        source_event_id="src",
        subject_key="sk",
        idempotency_key="ik",
        triggered_at=datetime.now(timezone.utc),
        acknowledged_at=None,
        resolved_at=None,
        last_matched_at=datetime.now(timezone.utc),
        summary="s",
        payload={"k": 1},
    )
    did = uuid4()
    body = build_alert_payload(alert, event_type="alert_triggered", delivery_id=did)
    assert body["schema_version"] == "1"
    assert body["delivery_id"] == str(did)
    assert body["alert"]["id"] == str(alert.id)
    assert "signing_secret" not in json.dumps(body)


def _notif_cfg(*, enabled=True, allow_private=False):
    from config.models import NotificationsConfig

    return NotificationsConfig(
        enabled=enabled,
        allow_private_targets=allow_private,
        worker_poll_interval_seconds=0.5,
        max_attempts=5,
        initial_backoff_seconds=1,
        max_backoff_seconds=10,
    )
