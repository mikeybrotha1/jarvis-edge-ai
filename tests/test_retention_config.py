"""Retention policy configuration tests (v0.10.0 phase 3)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import build_app_from_loaded_config, create_app
from config.loader import ConfigurationError, load_app_config
from config.models import OpsConfig, RetentionConfig
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_entity_schema,
    create_session_factory,
)


VALID_DATABASE_URL = "postgresql://user:password@localhost/database"


class RetentionConfigTests(unittest.TestCase):
    def test_defaults_disabled_and_dry_run(self) -> None:
        with patch.dict(
            os.environ,
            {"JARVIS_DATABASE_URL": VALID_DATABASE_URL},
            clear=True,
        ):
            cfg = load_app_config(root=Path(tempfile.mkdtemp()))

        ret = cfg.ops.retention
        self.assertIsInstance(ret, RetentionConfig)
        self.assertFalse(ret.enabled)
        self.assertTrue(ret.dry_run)
        self.assertEqual(ret.interval_seconds, 86400)
        self.assertEqual(ret.batch_size, 250)
        self.assertEqual(ret.max_batches_per_run, 4)
        self.assertFalse(ret.observations.enabled)
        self.assertEqual(ret.observations.keep_days, 30)
        self.assertFalse(ret.entities.enabled)
        self.assertEqual(ret.entities.keep_closed_days, 90)
        self.assertFalse(ret.zone_sessions.enabled)
        self.assertFalse(ret.alerts.enabled)
        self.assertFalse(ret.evaluator_state.enabled)
        self.assertFalse(ret.notification_deliveries.enabled)
        self.assertFalse(ret.any_domain_enabled())

    def test_yaml_loading(self) -> None:
        root = Path(tempfile.mkdtemp())
        cfg_path = root / "jarvis.yaml"
        cfg_path.write_text(
            """
database:
  url: postgresql://user:password@localhost/database
ops:
  retention:
    enabled: true
    dry_run: true
    interval_seconds: 3600
    batch_size: 100
    max_batches_per_run: 2
    observations:
      enabled: true
      keep_days: 14
    entities:
      keep_closed_days: 60
""",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_app_config(config_path=cfg_path, root=root)

        ret = cfg.ops.retention
        self.assertTrue(ret.enabled)
        self.assertTrue(ret.dry_run)
        self.assertEqual(ret.interval_seconds, 3600)
        self.assertEqual(ret.batch_size, 100)
        self.assertEqual(ret.max_batches_per_run, 2)
        self.assertTrue(ret.observations.enabled)
        self.assertEqual(ret.observations.keep_days, 14)
        self.assertFalse(ret.entities.enabled)
        self.assertEqual(ret.entities.keep_closed_days, 60)

    def test_environment_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_ENABLED": "true",
                "JARVIS_OPS_RETENTION_DRY_RUN": "true",
                "JARVIS_OPS_RETENTION_INTERVAL_SECONDS": "120",
                "JARVIS_OPS_RETENTION_BATCH_SIZE": "10",
                "JARVIS_OPS_RETENTION_MAX_BATCHES_PER_RUN": "3",
                "JARVIS_OPS_RETENTION_ALERTS_ENABLED": "true",
                "JARVIS_OPS_RETENTION_ALERTS_KEEP_RESOLVED_DAYS": "45",
            },
            clear=True,
        ):
            cfg = load_app_config(root=Path(tempfile.mkdtemp()))

        ret = cfg.ops.retention
        self.assertTrue(ret.enabled)
        self.assertTrue(ret.dry_run)
        self.assertEqual(ret.interval_seconds, 120)
        self.assertEqual(ret.batch_size, 10)
        self.assertEqual(ret.max_batches_per_run, 3)
        self.assertTrue(ret.alerts.enabled)
        self.assertEqual(ret.alerts.keep_resolved_days, 45)

    def test_min_max_bounds(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_INTERVAL_SECONDS": "60",
                "JARVIS_OPS_RETENTION_BATCH_SIZE": "1",
                "JARVIS_OPS_RETENTION_MAX_BATCHES_PER_RUN": "1",
                "JARVIS_OPS_RETENTION_OBSERVATIONS_KEEP_DAYS": "1",
            },
            clear=True,
        ):
            cfg = load_app_config(root=Path(tempfile.mkdtemp()))
        self.assertEqual(cfg.ops.retention.interval_seconds, 60)
        self.assertEqual(cfg.ops.retention.batch_size, 1)
        self.assertEqual(cfg.ops.retention.observations.keep_days, 1)

        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_INTERVAL_SECONDS": "604800",
                "JARVIS_OPS_RETENTION_BATCH_SIZE": "1000",
                "JARVIS_OPS_RETENTION_MAX_BATCHES_PER_RUN": "100",
                "JARVIS_OPS_RETENTION_ENTITIES_KEEP_CLOSED_DAYS": "3650",
            },
            clear=True,
        ):
            cfg = load_app_config(root=Path(tempfile.mkdtemp()))
        self.assertEqual(cfg.ops.retention.interval_seconds, 604800)
        self.assertEqual(cfg.ops.retention.batch_size, 1000)
        self.assertEqual(cfg.ops.retention.entities.keep_closed_days, 3650)

    def test_reject_out_of_bounds(self) -> None:
        cases = [
            {"JARVIS_OPS_RETENTION_INTERVAL_SECONDS": "59"},
            {"JARVIS_OPS_RETENTION_INTERVAL_SECONDS": "604801"},
            {"JARVIS_OPS_RETENTION_BATCH_SIZE": "0"},
            {"JARVIS_OPS_RETENTION_BATCH_SIZE": "1001"},
            {"JARVIS_OPS_RETENTION_MAX_BATCHES_PER_RUN": "0"},
            {"JARVIS_OPS_RETENTION_MAX_BATCHES_PER_RUN": "101"},
            {"JARVIS_OPS_RETENTION_OBSERVATIONS_KEEP_DAYS": "0"},
            {"JARVIS_OPS_RETENTION_OBSERVATIONS_KEEP_DAYS": "3651"},
        ]
        for extra in cases:
            env = {"JARVIS_DATABASE_URL": VALID_DATABASE_URL, **extra}
            with self.subTest(extra=extra):
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(ConfigurationError):
                        load_app_config(root=Path(tempfile.mkdtemp()))

    def test_reject_malformed_boolean_and_int(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_ENABLED": "maybe",
            },
            clear=True,
        ):
            with self.assertRaises(ConfigurationError) as ctx:
                load_app_config(root=Path(tempfile.mkdtemp()))
            self.assertIn("JARVIS_OPS_RETENTION_ENABLED", str(ctx.exception))

        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_BATCH_SIZE": "not-a-number",
            },
            clear=True,
        ):
            with self.assertRaises(ConfigurationError) as ctx:
                load_app_config(root=Path(tempfile.mkdtemp()))
            self.assertIn("JARVIS_OPS_RETENTION_BATCH_SIZE", str(ctx.exception))

    def test_independent_domain_enablement(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_ZONE_SESSIONS_ENABLED": "true",
                "JARVIS_OPS_RETENTION_NOTIFICATION_DELIVERIES_ENABLED": "true",
            },
            clear=True,
        ):
            cfg = load_app_config(root=Path(tempfile.mkdtemp()))
        ret = cfg.ops.retention
        self.assertFalse(ret.enabled)
        self.assertTrue(ret.zone_sessions.enabled)
        self.assertTrue(ret.notification_deliveries.enabled)
        self.assertFalse(ret.observations.enabled)
        self.assertTrue(ret.any_domain_enabled())

    def test_reject_destructive_noop(self) -> None:
        """enabled=true, dry_run=false, no domains → rejected."""

        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_ENABLED": "true",
                "JARVIS_OPS_RETENTION_DRY_RUN": "false",
            },
            clear=True,
        ):
            with self.assertRaises(ConfigurationError) as ctx:
                load_app_config(root=Path(tempfile.mkdtemp()))
            self.assertIn("dry_run=false", str(ctx.exception))

    def test_allow_destructive_when_domain_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_ENABLED": "true",
                "JARVIS_OPS_RETENTION_DRY_RUN": "false",
                "JARVIS_OPS_RETENTION_OBSERVATIONS_ENABLED": "true",
            },
            clear=True,
        ):
            cfg = load_app_config(root=Path(tempfile.mkdtemp()))
        self.assertTrue(cfg.ops.retention.enabled)
        self.assertFalse(cfg.ops.retention.dry_run)
        self.assertTrue(cfg.ops.retention.observations.enabled)

    def test_unknown_yaml_key_rejected(self) -> None:
        root = Path(tempfile.mkdtemp())
        cfg_path = root / "jarvis.yaml"
        cfg_path.write_text(
            """
database:
  url: postgresql://user:password@localhost/database
ops:
  retention:
    enabled: false
    unknown_field: true
""",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError) as ctx:
                load_app_config(config_path=cfg_path, root=root)
        self.assertIn("unknown_field", str(ctx.exception))

    def test_app_state_exposure(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_OPS_RETENTION_OBSERVATIONS_KEEP_DAYS": "21",
            },
            clear=True,
        ):
            app_config = load_app_config(root=Path(tempfile.mkdtemp()))

        engine = create_entity_engine("sqlite+pysqlite:///:memory:")
        create_entity_schema(engine)
        factory = create_session_factory(engine)
        app = create_app(
            session_factory=factory,
            enable_activity_stream=False,
            ops_config=app_config.ops,
        )
        self.assertIsInstance(app.state.ops_config, OpsConfig)
        self.assertIsInstance(app.state.retention_config, RetentionConfig)
        self.assertEqual(
            app.state.retention_config.observations.keep_days, 21
        )
        self.assertFalse(app.state.retention_config.enabled)

        with TestClient(app) as client:
            body = client.get("/api/v1/ops/status").json()
            self.assertIn("retention", body)
            # Phase 4: disabled config reports execution=disabled (not not_started).
            self.assertEqual(body["retention"]["execution"], "disabled")
            self.assertFalse(body["retention"]["enabled"])
            self.assertTrue(body["retention"]["dry_run"])
            self.assertEqual(
                body["retention"]["domains"]["observations"]["keep_days"],
                21,
            )
        engine.dispose()

    def test_build_app_from_loaded_config_wires_ops(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                "JARVIS_ACTIVITY_STREAM_ENABLED": "false",
                "JARVIS_OPS_RETENTION_ENABLED": "false",
            },
            clear=True,
        ):
            app_config = load_app_config(root=Path(tempfile.mkdtemp()))
        app = build_app_from_loaded_config(app_config)
        self.assertIsNotNone(app.state.ops_config)
        self.assertIsNotNone(app.state.retention_config)
        self.assertFalse(app.state.retention_config.enabled)


if __name__ == "__main__":
    unittest.main()
