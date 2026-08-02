"""Regression tests for ``python -m api`` configuration wiring."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import (
    build_app_from_loaded_config,
    create_app,
    create_app_from_config,
)
from config import load_app_config
from config.models import ActivityStreamConfig
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)


VALID_DATABASE_URL = "postgresql://user:password@localhost/database"


def _environ_with_activity_stream(**extra: str) -> dict[str, str]:
    env = {
        "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
        "JARVIS_ACTIVITY_STREAM_ENABLED": "true",
        "JARVIS_ACTIVITY_STREAM_NOTIFY_CHANNEL": "jarvis_activity",
    }
    env.update(extra)
    return env


def test_load_app_config_enables_activity_stream_from_env() -> None:
    with patch.dict(os.environ, _environ_with_activity_stream(), clear=True):
        cfg = load_app_config(root=Path("/tmp"))

    assert cfg.activity_stream.enabled is True
    assert cfg.activity_stream.notify_channel == "jarvis_activity"


def test_build_app_from_loaded_config_wires_activity_stream() -> None:
    """CLI construction path used by ``api.__main__`` must enable the stream."""

    with patch.dict(os.environ, _environ_with_activity_stream(), clear=True):
        app_config = load_app_config(root=Path("/tmp"))

    assert app_config.activity_stream.enabled is True

    application = build_app_from_loaded_config(app_config)

    assert application.state.activity_stream_enabled is True
    assert application.state.activity_stream_config is not None
    assert application.state.activity_stream_config.enabled is True
    assert application.state.activity_broker is not None
    # PostgreSQL URL ⇒ listener should be constructed for process-wide LISTEN.
    assert application.state.activity_listener is not None
    assert (
        application.state.activity_listener._channel
        == app_config.activity_stream.notify_channel
    )


def test_create_app_from_config_matches_main_wiring() -> None:
    """Uvicorn factory must use the same wiring as ``python -m api``."""

    with patch.dict(os.environ, _environ_with_activity_stream(), clear=True):
        application = create_app_from_config()

    assert application.state.activity_stream_enabled is True
    assert application.state.activity_broker is not None
    assert application.state.activity_listener is not None


def test_main_module_build_path_respects_disabled_stream() -> None:
    with patch.dict(
        os.environ,
        _environ_with_activity_stream(
            JARVIS_ACTIVITY_STREAM_ENABLED="false",
        ),
        clear=True,
    ):
        app_config = load_app_config(root=Path("/tmp"))
        application = build_app_from_loaded_config(app_config)

    assert app_config.activity_stream.enabled is False
    assert application.state.activity_stream_enabled is False
    assert application.state.activity_broker is None
    assert application.state.activity_listener is None


def test_main_module_does_not_drop_activity_stream_config() -> None:
    """Guard against reintroducing bare create_app() without stream config.

    Replicates the exact construction arguments that ``api.__main__`` must use
    via ``build_app_from_loaded_config``.
    """

    with patch.dict(os.environ, _environ_with_activity_stream(), clear=True):
        app_config = load_app_config(root=Path("/tmp"))

    # Simulate what a broken __main__ did: create_app without activity config.
    from api.app import create_app
    from services.entity_query_service import QueryLimits

    broken = create_app(
        database_url=app_config.database.url,
        limits=QueryLimits(
            entity_default_limit=app_config.api.default_limit,
            entity_maximum_limit=app_config.api.maximum_limit,
        ),
        create_schema=False,
    )
    assert broken.state.activity_stream_enabled is False

    # Correct path used by __main__ and create_app_from_config.
    fixed = build_app_from_loaded_config(app_config)
    assert fixed.state.activity_stream_enabled is True
    assert isinstance(
        fixed.state.activity_stream_config,
        ActivityStreamConfig,
    )


def test_load_app_config_allow_private_targets_from_env() -> None:
    with patch.dict(
        os.environ,
        {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS": "true",
        },
        clear=True,
    ):
        cfg = load_app_config(root=Path("/tmp"))
    assert cfg.notifications.allow_private_targets is True


def test_build_app_from_loaded_config_wires_allow_private_targets() -> None:
    """CLI path must pass NotificationsConfig into target service + provider."""

    with patch.dict(
        os.environ,
        {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS": "true",
            "JARVIS_ACTIVITY_STREAM_ENABLED": "false",
        },
        clear=True,
    ):
        app_config = load_app_config(root=Path("/tmp"))

    assert app_config.notifications.allow_private_targets is True

    application = build_app_from_loaded_config(app_config)
    assert application.state.notifications_config is not None
    assert (
        application.state.notifications_config.allow_private_targets is True
    )
    assert application.state.notifications_allow_private_targets is True
    svc = application.state.notification_target_service
    assert svc is not None
    assert svc.allow_private_targets is True


def test_cli_path_allow_private_targets_accepts_lan_webhook() -> None:
    """CLI path: env true → POST RFC1918 LAN target → 201 (not loopback)."""

    with patch.dict(
        os.environ,
        {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS": "true",
            "JARVIS_ACTIVITY_STREAM_ENABLED": "false",
        },
        clear=True,
    ):
        app_config = load_app_config(root=Path("/tmp"))

    assert app_config.notifications.allow_private_targets is True

    # Same notifications object as python -m api / build_app_from_loaded_config
    # (sqlite session_factory for request handling without live PostgreSQL).
    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    application = create_app(
        session_factory=factory,
        activity_stream_config=app_config.activity_stream,
        alerts_config=app_config.alerts,
        notifications_config=app_config.notifications,
        enable_activity_stream=False,
        create_schema=False,
    )
    assert application.state.notification_target_service.allow_private_targets is True

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/notification-targets",
            json={
                "name": "lan-smoke-hook",
                "url": "http://10.0.0.42:8090/",
                "enabled": True,
                "is_global": True,
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["url"] == "http://10.0.0.42:8090/"
    assert "signing_secret" not in body


def test_cli_path_loopback_rejected_even_when_private_allowed() -> None:
    """Loopback stays blocked with allow_private_targets=true (policy)."""

    with patch.dict(
        os.environ,
        {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS": "true",
            "JARVIS_ACTIVITY_STREAM_ENABLED": "false",
        },
        clear=True,
    ):
        app_config = load_app_config(root=Path("/tmp"))

    assert app_config.notifications.allow_private_targets is True

    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    application = create_app(
        session_factory=factory,
        activity_stream_config=app_config.activity_stream,
        alerts_config=app_config.alerts,
        notifications_config=app_config.notifications,
        enable_activity_stream=False,
        create_schema=False,
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/notification-targets",
            json={
                "name": "blocked-loopback",
                "url": "http://127.0.0.1:8090/",
                "enabled": True,
            },
        )
    assert response.status_code == 422, response.text
    assert "loopback" in response.json()["detail"].lower()


def test_cli_path_default_rejects_private_lan_webhook() -> None:
    """Production default: allow_private false → RFC1918 target → 422."""

    with patch.dict(
        os.environ,
        {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS": "false",
            "JARVIS_ACTIVITY_STREAM_ENABLED": "false",
        },
        clear=True,
    ):
        app_config = load_app_config(root=Path("/tmp"))

    assert app_config.notifications.allow_private_targets is False

    engine = create_entity_engine("sqlite+pysqlite:///:memory:")
    create_entity_schema(engine)
    factory = create_session_factory(engine)
    application = create_app(
        session_factory=factory,
        activity_stream_config=app_config.activity_stream,
        alerts_config=app_config.alerts,
        notifications_config=app_config.notifications,
        enable_activity_stream=False,
        create_schema=False,
    )
    assert application.state.notification_target_service.allow_private_targets is False

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/notification-targets",
            json={
                "name": "blocked-lan",
                "url": "http://192.168.1.10:8090/",
                "enabled": True,
            },
        )
    assert response.status_code == 422, response.text
    assert "private" in response.json()["detail"].lower()


if __name__ == "__main__":
    test_load_app_config_enables_activity_stream_from_env()
    test_build_app_from_loaded_config_wires_activity_stream()
    test_create_app_from_config_matches_main_wiring()
    test_main_module_build_path_respects_disabled_stream()
    test_main_module_does_not_drop_activity_stream_config()
    print("API CLI wiring tests passed.")
