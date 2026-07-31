"""Unit tests for the application configuration foundation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.loader import ConfigurationError, load_app_config


VALID_DATABASE_URL = "postgresql://user:password@localhost/database"


class AppConfigTests(unittest.TestCase):
    def test_defaults_with_database_url_only(self) -> None:
        with patch.dict(
            os.environ,
            {"JARVIS_DATABASE_URL": VALID_DATABASE_URL},
            clear=True,
        ):
            cfg = load_app_config(root=Path(tempfile.mkdtemp()))

        self.assertEqual(cfg.database.url, VALID_DATABASE_URL)
        self.assertEqual(cfg.camera.device, 0)
        self.assertEqual(cfg.camera.width, 1280)
        self.assertEqual(cfg.camera.height, 720)
        self.assertEqual(cfg.camera.fps, 30)
        self.assertEqual(cfg.camera.source_name, "azure_kinect")
        self.assertEqual(
            cfg.detector.model_path,
            "/usr/local/hailo/resources/models/hailo10h/yolov6n.hef",
        )
        self.assertEqual(cfg.detector.confidence_threshold, 0.40)
        self.assertEqual(cfg.detector.timeout_seconds, 10.0)
        self.assertEqual(cfg.memory.source, "vision_memory")
        self.assertEqual(cfg.memory.iou_threshold, 0.30)
        self.assertEqual(cfg.memory.max_missed_frames, 8)
        self.assertEqual(cfg.entity_memory.identity_strategy, "tracker_id")
        self.assertEqual(
            cfg.entity_memory.snapshot_min_interval_seconds,
            0.0,
        )
        self.assertTrue(cfg.entity_memory.snapshot_on_update)
        self.assertFalse(cfg.api.enabled)
        self.assertEqual(cfg.api.host, "0.0.0.0")
        self.assertEqual(cfg.api.port, 8080)
        self.assertEqual(cfg.api.default_limit, 50)
        self.assertEqual(cfg.api.maximum_limit, 200)
        self.assertEqual(cfg.logging.level, "INFO")
        self.assertEqual(cfg.logging.log_file, "logs/jarvis.log")
        self.assertEqual(cfg.runtime.platform, "raspberry_pi_5")
        self.assertEqual(cfg.runtime.application, "jarvis-edge-ai")

    def test_yaml_overrides_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "jarvis.yaml"
            path.write_text(
                "\n".join(
                    [
                        f"database:",
                        f"  url: {VALID_DATABASE_URL}",
                        "camera:",
                        "  device: 2",
                        "  width: 640",
                        "  height: 480",
                        "  fps: 15",
                        "  source_name: usb_camera",
                        "detector:",
                        "  confidence_threshold: 0.55",
                        "  timeout_seconds: 5.5",
                        "memory:",
                        "  source: custom_memory",
                        "  iou_threshold: 0.25",
                        "  max_missed_frames: 3",
                        "logging:",
                        "  level: DEBUG",
                        "  log_file: logs/custom.log",
                        "runtime:",
                        "  platform: test_platform",
                        "  application: test_app",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                cfg = load_app_config(
                    config_path=path,
                    environ={},
                    root=Path(temp_directory),
                )

        self.assertEqual(cfg.camera.device, 2)
        self.assertEqual(cfg.camera.width, 640)
        self.assertEqual(cfg.camera.height, 480)
        self.assertEqual(cfg.camera.fps, 15)
        self.assertEqual(cfg.camera.source_name, "usb_camera")
        self.assertEqual(cfg.detector.confidence_threshold, 0.55)
        self.assertEqual(cfg.detector.timeout_seconds, 5.5)
        self.assertEqual(cfg.memory.source, "custom_memory")
        self.assertEqual(cfg.memory.iou_threshold, 0.25)
        self.assertEqual(cfg.memory.max_missed_frames, 3)
        self.assertEqual(cfg.logging.level, "DEBUG")
        self.assertEqual(cfg.logging.log_file, "logs/custom.log")
        self.assertEqual(cfg.runtime.platform, "test_platform")
        self.assertEqual(cfg.runtime.application, "test_app")

    def test_environment_overrides_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "jarvis.yaml"
            path.write_text(
                "\n".join(
                    [
                        "database:",
                        f"  url: {VALID_DATABASE_URL}",
                        "camera:",
                        "  device: 0",
                        "  width: 1280",
                        "memory:",
                        "  iou_threshold: 0.30",
                    ]
                ),
                encoding="utf-8",
            )

            environ = {
                "JARVIS_CAMERA_DEVICE": "4",
                "JARVIS_CAMERA_WIDTH": "1920",
                "JARVIS_MEMORY_IOU_THRESHOLD": "0.75",
                "JARVIS_DATABASE_URL": (
                    "postgresql://env_user:env_pass@db/env_db"
                ),
            }

            with patch.dict(os.environ, environ, clear=True):
                cfg = load_app_config(
                    config_path=path,
                    environ=environ,
                    root=Path(temp_directory),
                )

        self.assertEqual(cfg.camera.device, 4)
        self.assertEqual(cfg.camera.width, 1920)
        self.assertEqual(cfg.memory.iou_threshold, 0.75)
        self.assertEqual(
            cfg.database.url,
            "postgresql://env_user:env_pass@db/env_db",
        )

    def test_explicit_missing_config_path_raises(self) -> None:
        missing = Path("/tmp/jarvis-does-not-exist-config.yaml")
        with patch.dict(
            os.environ,
            {"JARVIS_DATABASE_URL": VALID_DATABASE_URL},
            clear=True,
        ):
            with self.assertRaises(ConfigurationError) as context:
                load_app_config(
                    config_path=missing,
                    environ={"JARVIS_DATABASE_URL": VALID_DATABASE_URL},
                )

        self.assertIn("not found", str(context.exception).lower())

    def test_jarvis_config_path_missing_raises(self) -> None:
        environ = {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_CONFIG_PATH": "/tmp/jarvis-missing-via-env.yaml",
        }
        with patch.dict(os.environ, environ, clear=True):
            with self.assertRaises(ConfigurationError) as context:
                load_app_config(environ=environ)

        message = str(context.exception)
        self.assertIn("JARVIS_CONFIG_PATH", message)
        self.assertIn("does not exist", message)

    def test_implicit_missing_default_config_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            environ = {"JARVIS_DATABASE_URL": VALID_DATABASE_URL}
            with patch.dict(os.environ, environ, clear=True):
                cfg = load_app_config(environ=environ, root=root)

        self.assertEqual(cfg.camera.device, 0)
        self.assertEqual(cfg.memory.iou_threshold, 0.30)
        self.assertEqual(cfg.database.url, VALID_DATABASE_URL)

    def test_implicit_default_config_loaded_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "jarvis.yaml").write_text(
                "\n".join(
                    [
                        "database:",
                        f"  url: {VALID_DATABASE_URL}",
                        "camera:",
                        "  device: 7",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                cfg = load_app_config(environ={}, root=root)

        self.assertEqual(cfg.camera.device, 7)

    def test_missing_database_url_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError) as context:
                    load_app_config(
                        environ={},
                        root=Path(temp_directory),
                    )

        self.assertIn("database.url", str(context.exception))

    def test_invalid_database_url_scheme_raises(self) -> None:
        environ = {"JARVIS_DATABASE_URL": "mysql://localhost/db"}
        with patch.dict(os.environ, environ, clear=True):
            with self.assertRaises(ConfigurationError) as context:
                load_app_config(
                    environ=environ,
                    root=Path(tempfile.mkdtemp()),
                )

        self.assertIn("PostgreSQL", str(context.exception))

    def test_invalid_environment_integer_raises(self) -> None:
        environ = {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_CAMERA_DEVICE": "not-a-number",
        }
        with patch.dict(os.environ, environ, clear=True):
            with self.assertRaises(ConfigurationError) as context:
                load_app_config(
                    environ=environ,
                    root=Path(tempfile.mkdtemp()),
                )

        self.assertIn("JARVIS_CAMERA_DEVICE", str(context.exception))

    def test_invalid_environment_float_raises(self) -> None:
        environ = {
            "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
            "JARVIS_MEMORY_IOU_THRESHOLD": "abc",
        }
        with patch.dict(os.environ, environ, clear=True):
            with self.assertRaises(ConfigurationError) as context:
                load_app_config(
                    environ=environ,
                    root=Path(tempfile.mkdtemp()),
                )

        self.assertIn(
            "JARVIS_MEMORY_IOU_THRESHOLD",
            str(context.exception),
        )

    def test_invalid_ranges_raise(self) -> None:
        cases = [
            {"JARVIS_CAMERA_DEVICE": "-1"},
            {"JARVIS_CAMERA_WIDTH": "0"},
            {"JARVIS_CAMERA_HEIGHT": "-5"},
            {"JARVIS_CAMERA_FPS": "0"},
            {"JARVIS_DETECTOR_CONFIDENCE_THRESHOLD": "1.5"},
            {"JARVIS_MEMORY_IOU_THRESHOLD": "-0.1"},
            {"JARVIS_DETECTOR_TIMEOUT_SECONDS": "0"},
            {"JARVIS_MEMORY_MAX_MISSED_FRAMES": "-2"},
            {"JARVIS_LOG_LEVEL": "VERBOSE"},
            {"JARVIS_CAMERA_SOURCE_NAME": "   "},
        ]

        for extra in cases:
            with self.subTest(extra=extra):
                environ = {
                    "JARVIS_DATABASE_URL": VALID_DATABASE_URL,
                    **extra,
                }
                with patch.dict(os.environ, environ, clear=True):
                    with self.assertRaises(ConfigurationError):
                        load_app_config(
                            environ=environ,
                            root=Path(tempfile.mkdtemp()),
                        )

    def test_unknown_yaml_section_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "bad.yaml"
            path.write_text(
                "\n".join(
                    [
                        f"database:",
                        f"  url: {VALID_DATABASE_URL}",
                        "plugins:",
                        "  enabled: true",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError) as context:
                    load_app_config(
                        config_path=path,
                        environ={},
                        root=Path(temp_directory),
                    )

        self.assertIn("Unknown configuration section", str(context.exception))
        self.assertIn("plugins", str(context.exception))

    def test_unknown_yaml_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "bad.yaml"
            path.write_text(
                "\n".join(
                    [
                        "database:",
                        f"  url: {VALID_DATABASE_URL}",
                        "camera:",
                        "  device: 0",
                        "  bitrate: 5000",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError) as context:
                    load_app_config(
                        config_path=path,
                        environ={},
                        root=Path(temp_directory),
                    )

        message = str(context.exception)
        self.assertIn("Unknown configuration key", message)
        self.assertIn("camera.bitrate", message)

    def test_postgres_scheme_accepted(self) -> None:
        environ = {
            "JARVIS_DATABASE_URL": "postgres://user:pass@localhost/db",
        }
        with patch.dict(os.environ, environ, clear=True):
            cfg = load_app_config(
                environ=environ,
                root=Path(tempfile.mkdtemp()),
            )

        self.assertEqual(
            cfg.database.url,
            "postgres://user:pass@localhost/db",
        )


if __name__ == "__main__":
    unittest.main()
