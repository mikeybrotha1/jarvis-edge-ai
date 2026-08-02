"""Load, merge, and validate Jarvis application configuration.

Precedence (highest wins):

1. Environment variables
2. Optional YAML file
3. Built-in defaults
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from config.models import (
    ActivityStreamConfig,
    AlertsConfig,
    ApiConfig,
    AppConfig,
    CameraConfig,
    DatabaseConfig,
    DetectorConfig,
    EntityMemoryConfig,
    LoggingConfig,
    MemoryConfig,
    NotificationsConfig,
    RuntimeConfig,
    SpatialConfig,
    TimelineConfig,
)


class ConfigurationError(RuntimeError):
    """Raised when application configuration is missing or invalid."""


_DEFAULT_CONFIG_RELATIVE = Path("config") / "jarvis.yaml"

_SECTION_FIELDS: dict[str, frozenset[str]] = {
    "database": frozenset({"url"}),
    "camera": frozenset(
        {"device", "width", "height", "fps", "source_name"}
    ),
    "detector": frozenset(
        {
            "model_path",
            "confidence_threshold",
            "timeout_seconds",
        }
    ),
    "memory": frozenset(
        {"source", "iou_threshold", "max_missed_frames"}
    ),
    "entity_memory": frozenset(
        {
            "identity_strategy",
            "snapshot_min_interval_seconds",
            "snapshot_on_update",
        }
    ),
    "api": frozenset(
        {
            "enabled",
            "host",
            "port",
            "default_limit",
            "maximum_limit",
        }
    ),
    "timeline": frozenset(
        {
            "default_limit",
            "maximum_limit",
        }
    ),
    "activity_stream": frozenset(
        {
            "enabled",
            "notify_channel",
            "observation_notifications_enabled",
            "observation_min_interval_seconds",
            "client_queue_size",
            "heartbeat_interval_seconds",
            "max_connections",
            "reconnect_initial_seconds",
            "reconnect_max_seconds",
        }
    ),
    "spatial": frozenset(
        {
            "enabled",
            "position_strategy",
            "enter_confirm_observations",
            "exit_confirm_observations",
            "lost_track_timeout_seconds",
            "maximum_zones_per_camera",
            "occupancy_stale_seconds",
            "publish_occupancy_changes",
        }
    ),
    "alerts": frozenset(
        {
            "enabled",
            "consumer_name",
            "queue_size",
            "reconcile_interval_seconds",
            "reconcile_batch_size",
            "replay_overlap_seconds",
            "max_rules",
            "default_cooldown_seconds",
            "max_metadata_bytes",
            "startup_catchup_limit",
            "timezone_default",
        }
    ),
    "notifications": frozenset(
        {
            "enabled",
            "worker_poll_interval_seconds",
            "max_attempts",
            "initial_backoff_seconds",
            "max_backoff_seconds",
            "backoff_multiplier",
            "request_timeout_seconds",
            "max_concurrent_deliveries",
            "batch_size",
            "lock_timeout_seconds",
            "max_request_bytes",
            "max_response_bytes",
            "allow_private_targets",
            "retention_days",
            "worker_id",
        }
    ),
    "logging": frozenset({"level", "log_file"}),
    "runtime": frozenset({"platform", "application"}),
}

_VALID_IDENTITY_STRATEGIES = frozenset({"tracker_id"})
_VALID_POSITION_STRATEGIES = frozenset({"bottom_center", "center"})

_KNOWN_SECTIONS = frozenset(_SECTION_FIELDS)

_VALID_LOG_LEVELS = frozenset(
    {
        "CRITICAL",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
        "NOTSET",
    }
)


def project_root() -> Path:
    """Return the repository root (parent of ``src/``)."""

    return Path(__file__).resolve().parents[2]


def load_app_config(
    *,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> AppConfig:
    """Load typed application configuration.

    Parameters
    ----------
    config_path:
        Explicit YAML path. When omitted, ``JARVIS_CONFIG_PATH`` is used
        if set; otherwise ``config/jarvis.yaml`` under the project root is
        used when that file exists.
    environ:
        Environment mapping (defaults to ``os.environ``). Intended for tests.
    root:
        Project root used to resolve the implicit default config path.
    """

    env = dict(os.environ if environ is None else environ)
    base = root if root is not None else project_root()

    values = _default_values()
    yaml_path = _resolve_config_path(
        config_path=config_path,
        environ=env,
        root=base,
    )

    if yaml_path is not None:
        yaml_data = _load_yaml_file(yaml_path)
        _merge_yaml(values, yaml_data)

    _apply_env_overrides(values, env)
    _validate(values)

    return _build_config(values)


def _default_values() -> dict[str, dict[str, Any]]:
    defaults = AppConfig()
    return {
        "database": {"url": defaults.database.url},
        "camera": {
            "device": defaults.camera.device,
            "width": defaults.camera.width,
            "height": defaults.camera.height,
            "fps": defaults.camera.fps,
            "source_name": defaults.camera.source_name,
        },
        "detector": {
            "model_path": defaults.detector.model_path,
            "confidence_threshold": (
                defaults.detector.confidence_threshold
            ),
            "timeout_seconds": defaults.detector.timeout_seconds,
        },
        "memory": {
            "source": defaults.memory.source,
            "iou_threshold": defaults.memory.iou_threshold,
            "max_missed_frames": defaults.memory.max_missed_frames,
        },
        "entity_memory": {
            "identity_strategy": (
                defaults.entity_memory.identity_strategy
            ),
            "snapshot_min_interval_seconds": (
                defaults.entity_memory.snapshot_min_interval_seconds
            ),
            "snapshot_on_update": (
                defaults.entity_memory.snapshot_on_update
            ),
        },
        "api": {
            "enabled": defaults.api.enabled,
            "host": defaults.api.host,
            "port": defaults.api.port,
            "default_limit": defaults.api.default_limit,
            "maximum_limit": defaults.api.maximum_limit,
        },
        "timeline": {
            "default_limit": defaults.timeline.default_limit,
            "maximum_limit": defaults.timeline.maximum_limit,
        },
        "activity_stream": {
            "enabled": defaults.activity_stream.enabled,
            "notify_channel": defaults.activity_stream.notify_channel,
            "observation_notifications_enabled": (
                defaults.activity_stream.observation_notifications_enabled
            ),
            "observation_min_interval_seconds": (
                defaults.activity_stream.observation_min_interval_seconds
            ),
            "client_queue_size": (
                defaults.activity_stream.client_queue_size
            ),
            "heartbeat_interval_seconds": (
                defaults.activity_stream.heartbeat_interval_seconds
            ),
            "max_connections": defaults.activity_stream.max_connections,
            "reconnect_initial_seconds": (
                defaults.activity_stream.reconnect_initial_seconds
            ),
            "reconnect_max_seconds": (
                defaults.activity_stream.reconnect_max_seconds
            ),
        },
        "spatial": {
            "enabled": defaults.spatial.enabled,
            "position_strategy": defaults.spatial.position_strategy,
            "enter_confirm_observations": (
                defaults.spatial.enter_confirm_observations
            ),
            "exit_confirm_observations": (
                defaults.spatial.exit_confirm_observations
            ),
            "lost_track_timeout_seconds": (
                defaults.spatial.lost_track_timeout_seconds
            ),
            "maximum_zones_per_camera": (
                defaults.spatial.maximum_zones_per_camera
            ),
            "occupancy_stale_seconds": (
                defaults.spatial.occupancy_stale_seconds
            ),
            "publish_occupancy_changes": (
                defaults.spatial.publish_occupancy_changes
            ),
        },
        "alerts": {
            "enabled": defaults.alerts.enabled,
            "consumer_name": defaults.alerts.consumer_name,
            "queue_size": defaults.alerts.queue_size,
            "reconcile_interval_seconds": (
                defaults.alerts.reconcile_interval_seconds
            ),
            "reconcile_batch_size": defaults.alerts.reconcile_batch_size,
            "replay_overlap_seconds": defaults.alerts.replay_overlap_seconds,
            "max_rules": defaults.alerts.max_rules,
            "default_cooldown_seconds": (
                defaults.alerts.default_cooldown_seconds
            ),
            "max_metadata_bytes": defaults.alerts.max_metadata_bytes,
            "startup_catchup_limit": defaults.alerts.startup_catchup_limit,
            "timezone_default": defaults.alerts.timezone_default,
        },
        "notifications": {
            "enabled": defaults.notifications.enabled,
            "worker_poll_interval_seconds": (
                defaults.notifications.worker_poll_interval_seconds
            ),
            "max_attempts": defaults.notifications.max_attempts,
            "initial_backoff_seconds": (
                defaults.notifications.initial_backoff_seconds
            ),
            "max_backoff_seconds": defaults.notifications.max_backoff_seconds,
            "backoff_multiplier": defaults.notifications.backoff_multiplier,
            "request_timeout_seconds": (
                defaults.notifications.request_timeout_seconds
            ),
            "max_concurrent_deliveries": (
                defaults.notifications.max_concurrent_deliveries
            ),
            "batch_size": defaults.notifications.batch_size,
            "lock_timeout_seconds": (
                defaults.notifications.lock_timeout_seconds
            ),
            "max_request_bytes": defaults.notifications.max_request_bytes,
            "max_response_bytes": defaults.notifications.max_response_bytes,
            "allow_private_targets": (
                defaults.notifications.allow_private_targets
            ),
            "retention_days": defaults.notifications.retention_days,
            "worker_id": defaults.notifications.worker_id,
        },
        "logging": {
            "level": defaults.logging.level,
            "log_file": defaults.logging.log_file,
        },
        "runtime": {
            "platform": defaults.runtime.platform,
            "application": defaults.runtime.application,
        },
    }


def _resolve_config_path(
    *,
    config_path: str | Path | None,
    environ: Mapping[str, str],
    root: Path,
) -> Path | None:
    if config_path is not None:
        path = Path(config_path)
        if not path.is_file():
            raise ConfigurationError(
                f"Configuration file not found: {path}"
            )
        return path

    env_path = environ.get("JARVIS_CONFIG_PATH", "").strip()
    if env_path:
        path = Path(env_path)
        if not path.is_file():
            raise ConfigurationError(
                "JARVIS_CONFIG_PATH is set but the file does not exist: "
                f"{path}"
            )
        return path

    default_path = root / _DEFAULT_CONFIG_RELATIVE
    if default_path.is_file():
        return default_path

    return None


def _load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(
            f"Unable to read configuration file {path}: {error}"
        ) from error

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise ConfigurationError(
            f"Invalid YAML in configuration file {path}: {error}"
        ) from error

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Configuration file {path} must contain a YAML mapping "
            "at the top level."
        )

    return data


def _merge_yaml(
    values: dict[str, dict[str, Any]],
    yaml_data: dict[str, Any],
) -> None:
    for section, section_data in yaml_data.items():
        if section not in _KNOWN_SECTIONS:
            raise ConfigurationError(
                f"Unknown configuration section: {section!r}. "
                f"Known sections: {', '.join(sorted(_KNOWN_SECTIONS))}."
            )

        if section_data is None:
            continue

        if not isinstance(section_data, dict):
            raise ConfigurationError(
                f"Configuration section {section!r} must be a mapping."
            )

        allowed = _SECTION_FIELDS[section]
        for key, value in section_data.items():
            if key not in allowed:
                raise ConfigurationError(
                    f"Unknown configuration key: {section}.{key}. "
                    f"Allowed keys: {', '.join(sorted(allowed))}."
                )
            values[section][key] = value


def _apply_env_overrides(
    values: dict[str, dict[str, Any]],
    environ: Mapping[str, str],
) -> None:
    _set_env_string(
        values,
        "database",
        "url",
        environ,
        "JARVIS_DATABASE_URL",
    )
    _set_env_int(
        values,
        "camera",
        "device",
        environ,
        "JARVIS_CAMERA_DEVICE",
    )
    _set_env_int(
        values,
        "camera",
        "width",
        environ,
        "JARVIS_CAMERA_WIDTH",
    )
    _set_env_int(
        values,
        "camera",
        "height",
        environ,
        "JARVIS_CAMERA_HEIGHT",
    )
    _set_env_int(
        values,
        "camera",
        "fps",
        environ,
        "JARVIS_CAMERA_FPS",
    )
    _set_env_string(
        values,
        "camera",
        "source_name",
        environ,
        "JARVIS_CAMERA_SOURCE_NAME",
    )
    _set_env_string(
        values,
        "detector",
        "model_path",
        environ,
        "JARVIS_DETECTOR_MODEL_PATH",
    )
    _set_env_float(
        values,
        "detector",
        "confidence_threshold",
        environ,
        "JARVIS_DETECTOR_CONFIDENCE_THRESHOLD",
    )
    _set_env_float(
        values,
        "detector",
        "timeout_seconds",
        environ,
        "JARVIS_DETECTOR_TIMEOUT_SECONDS",
    )
    _set_env_string(
        values,
        "memory",
        "source",
        environ,
        "JARVIS_MEMORY_SOURCE",
    )
    _set_env_float(
        values,
        "memory",
        "iou_threshold",
        environ,
        "JARVIS_MEMORY_IOU_THRESHOLD",
    )
    _set_env_int(
        values,
        "memory",
        "max_missed_frames",
        environ,
        "JARVIS_MEMORY_MAX_MISSED_FRAMES",
    )
    _set_env_string(
        values,
        "entity_memory",
        "identity_strategy",
        environ,
        "JARVIS_ENTITY_MEMORY_IDENTITY_STRATEGY",
    )
    _set_env_float(
        values,
        "entity_memory",
        "snapshot_min_interval_seconds",
        environ,
        "JARVIS_ENTITY_MEMORY_SNAPSHOT_MIN_INTERVAL_SECONDS",
    )
    _set_env_bool(
        values,
        "entity_memory",
        "snapshot_on_update",
        environ,
        "JARVIS_ENTITY_MEMORY_SNAPSHOT_ON_UPDATE",
    )
    _set_env_bool(
        values,
        "api",
        "enabled",
        environ,
        "JARVIS_API_ENABLED",
    )
    _set_env_string(
        values,
        "api",
        "host",
        environ,
        "JARVIS_API_HOST",
    )
    _set_env_int(
        values,
        "api",
        "port",
        environ,
        "JARVIS_API_PORT",
    )
    _set_env_int(
        values,
        "api",
        "default_limit",
        environ,
        "JARVIS_API_DEFAULT_LIMIT",
    )
    _set_env_int(
        values,
        "api",
        "maximum_limit",
        environ,
        "JARVIS_API_MAXIMUM_LIMIT",
    )
    _set_env_int(
        values,
        "timeline",
        "default_limit",
        environ,
        "JARVIS_TIMELINE_DEFAULT_LIMIT",
    )
    _set_env_int(
        values,
        "timeline",
        "maximum_limit",
        environ,
        "JARVIS_TIMELINE_MAXIMUM_LIMIT",
    )
    _set_env_bool(
        values,
        "activity_stream",
        "enabled",
        environ,
        "JARVIS_ACTIVITY_STREAM_ENABLED",
    )
    _set_env_string(
        values,
        "activity_stream",
        "notify_channel",
        environ,
        "JARVIS_ACTIVITY_STREAM_NOTIFY_CHANNEL",
    )
    _set_env_bool(
        values,
        "activity_stream",
        "observation_notifications_enabled",
        environ,
        "JARVIS_ACTIVITY_STREAM_OBSERVATION_NOTIFICATIONS_ENABLED",
    )
    _set_env_float(
        values,
        "activity_stream",
        "observation_min_interval_seconds",
        environ,
        "JARVIS_ACTIVITY_STREAM_OBSERVATION_MIN_INTERVAL_SECONDS",
    )
    _set_env_int(
        values,
        "activity_stream",
        "client_queue_size",
        environ,
        "JARVIS_ACTIVITY_STREAM_CLIENT_QUEUE_SIZE",
    )
    _set_env_float(
        values,
        "activity_stream",
        "heartbeat_interval_seconds",
        environ,
        "JARVIS_ACTIVITY_STREAM_HEARTBEAT_INTERVAL_SECONDS",
    )
    _set_env_int(
        values,
        "activity_stream",
        "max_connections",
        environ,
        "JARVIS_ACTIVITY_STREAM_MAX_CONNECTIONS",
    )
    _set_env_float(
        values,
        "activity_stream",
        "reconnect_initial_seconds",
        environ,
        "JARVIS_ACTIVITY_STREAM_RECONNECT_INITIAL_SECONDS",
    )
    _set_env_float(
        values,
        "activity_stream",
        "reconnect_max_seconds",
        environ,
        "JARVIS_ACTIVITY_STREAM_RECONNECT_MAX_SECONDS",
    )
    _set_env_bool(
        values,
        "spatial",
        "enabled",
        environ,
        "JARVIS_SPATIAL_ENABLED",
    )
    _set_env_string(
        values,
        "spatial",
        "position_strategy",
        environ,
        "JARVIS_SPATIAL_POSITION_STRATEGY",
    )
    _set_env_int(
        values,
        "spatial",
        "enter_confirm_observations",
        environ,
        "JARVIS_SPATIAL_ENTER_CONFIRM_OBSERVATIONS",
    )
    _set_env_int(
        values,
        "spatial",
        "exit_confirm_observations",
        environ,
        "JARVIS_SPATIAL_EXIT_CONFIRM_OBSERVATIONS",
    )
    _set_env_float(
        values,
        "spatial",
        "lost_track_timeout_seconds",
        environ,
        "JARVIS_SPATIAL_LOST_TRACK_TIMEOUT_SECONDS",
    )
    _set_env_int(
        values,
        "spatial",
        "maximum_zones_per_camera",
        environ,
        "JARVIS_SPATIAL_MAXIMUM_ZONES_PER_CAMERA",
    )
    _set_env_float(
        values,
        "spatial",
        "occupancy_stale_seconds",
        environ,
        "JARVIS_SPATIAL_OCCUPANCY_STALE_SECONDS",
    )
    _set_env_bool(
        values,
        "spatial",
        "publish_occupancy_changes",
        environ,
        "JARVIS_SPATIAL_PUBLISH_OCCUPANCY_CHANGES",
    )
    _set_env_bool(
        values, "alerts", "enabled", environ, "JARVIS_ALERTS_ENABLED"
    )
    _set_env_string(
        values,
        "alerts",
        "consumer_name",
        environ,
        "JARVIS_ALERTS_CONSUMER_NAME",
    )
    _set_env_int(
        values, "alerts", "queue_size", environ, "JARVIS_ALERTS_QUEUE_SIZE"
    )
    _set_env_float(
        values,
        "alerts",
        "reconcile_interval_seconds",
        environ,
        "JARVIS_ALERTS_RECONCILE_INTERVAL_SECONDS",
    )
    _set_env_int(
        values,
        "alerts",
        "reconcile_batch_size",
        environ,
        "JARVIS_ALERTS_RECONCILE_BATCH_SIZE",
    )
    _set_env_float(
        values,
        "alerts",
        "replay_overlap_seconds",
        environ,
        "JARVIS_ALERTS_REPLAY_OVERLAP_SECONDS",
    )
    _set_env_int(
        values, "alerts", "max_rules", environ, "JARVIS_ALERTS_MAX_RULES"
    )
    _set_env_int(
        values,
        "alerts",
        "default_cooldown_seconds",
        environ,
        "JARVIS_ALERTS_DEFAULT_COOLDOWN_SECONDS",
    )
    _set_env_int(
        values,
        "alerts",
        "max_metadata_bytes",
        environ,
        "JARVIS_ALERTS_MAX_METADATA_BYTES",
    )
    _set_env_int(
        values,
        "alerts",
        "startup_catchup_limit",
        environ,
        "JARVIS_ALERTS_STARTUP_CATCHUP_LIMIT",
    )
    _set_env_string(
        values,
        "alerts",
        "timezone_default",
        environ,
        "JARVIS_ALERTS_TIMEZONE_DEFAULT",
    )
    _set_env_bool(
        values,
        "notifications",
        "enabled",
        environ,
        "JARVIS_NOTIFICATIONS_ENABLED",
    )
    _set_env_float(
        values,
        "notifications",
        "worker_poll_interval_seconds",
        environ,
        "JARVIS_NOTIFICATIONS_WORKER_POLL_INTERVAL_SECONDS",
    )
    _set_env_int(
        values,
        "notifications",
        "max_attempts",
        environ,
        "JARVIS_NOTIFICATIONS_MAX_ATTEMPTS",
    )
    _set_env_float(
        values,
        "notifications",
        "initial_backoff_seconds",
        environ,
        "JARVIS_NOTIFICATIONS_INITIAL_BACKOFF_SECONDS",
    )
    _set_env_float(
        values,
        "notifications",
        "max_backoff_seconds",
        environ,
        "JARVIS_NOTIFICATIONS_MAX_BACKOFF_SECONDS",
    )
    _set_env_float(
        values,
        "notifications",
        "backoff_multiplier",
        environ,
        "JARVIS_NOTIFICATIONS_BACKOFF_MULTIPLIER",
    )
    _set_env_float(
        values,
        "notifications",
        "request_timeout_seconds",
        environ,
        "JARVIS_NOTIFICATIONS_REQUEST_TIMEOUT_SECONDS",
    )
    _set_env_int(
        values,
        "notifications",
        "max_concurrent_deliveries",
        environ,
        "JARVIS_NOTIFICATIONS_MAX_CONCURRENT_DELIVERIES",
    )
    _set_env_int(
        values,
        "notifications",
        "batch_size",
        environ,
        "JARVIS_NOTIFICATIONS_BATCH_SIZE",
    )
    _set_env_float(
        values,
        "notifications",
        "lock_timeout_seconds",
        environ,
        "JARVIS_NOTIFICATIONS_LOCK_TIMEOUT_SECONDS",
    )
    _set_env_int(
        values,
        "notifications",
        "max_request_bytes",
        environ,
        "JARVIS_NOTIFICATIONS_MAX_REQUEST_BYTES",
    )
    _set_env_int(
        values,
        "notifications",
        "max_response_bytes",
        environ,
        "JARVIS_NOTIFICATIONS_MAX_RESPONSE_BYTES",
    )
    _set_env_bool(
        values,
        "notifications",
        "allow_private_targets",
        environ,
        "JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS",
    )
    _set_env_int(
        values,
        "notifications",
        "retention_days",
        environ,
        "JARVIS_NOTIFICATIONS_RETENTION_DAYS",
    )
    _set_env_string(
        values,
        "notifications",
        "worker_id",
        environ,
        "JARVIS_NOTIFICATIONS_WORKER_ID",
    )
    _set_env_string(
        values,
        "logging",
        "level",
        environ,
        "JARVIS_LOG_LEVEL",
    )
    _set_env_string(
        values,
        "logging",
        "log_file",
        environ,
        "JARVIS_LOG_FILE",
    )
    _set_env_string(
        values,
        "runtime",
        "platform",
        environ,
        "JARVIS_RUNTIME_PLATFORM",
    )
    _set_env_string(
        values,
        "runtime",
        "application",
        environ,
        "JARVIS_RUNTIME_APPLICATION",
    )


def _set_env_string(
    values: dict[str, dict[str, Any]],
    section: str,
    field: str,
    environ: Mapping[str, str],
    env_name: str,
) -> None:
    if env_name not in environ:
        return
    values[section][field] = environ[env_name].strip()


def _set_env_int(
    values: dict[str, dict[str, Any]],
    section: str,
    field: str,
    environ: Mapping[str, str],
    env_name: str,
) -> None:
    if env_name not in environ:
        return
    raw = environ[env_name].strip()
    try:
        values[section][field] = int(raw)
    except ValueError as error:
        raise ConfigurationError(
            f"Invalid integer for {env_name}: {raw!r}"
        ) from error


def _set_env_float(
    values: dict[str, dict[str, Any]],
    section: str,
    field: str,
    environ: Mapping[str, str],
    env_name: str,
) -> None:
    if env_name not in environ:
        return
    raw = environ[env_name].strip()
    try:
        values[section][field] = float(raw)
    except ValueError as error:
        raise ConfigurationError(
            f"Invalid number for {env_name}: {raw!r}"
        ) from error


def _set_env_bool(
    values: dict[str, dict[str, Any]],
    section: str,
    field: str,
    environ: Mapping[str, str],
    env_name: str,
) -> None:
    if env_name not in environ:
        return
    raw = environ[env_name].strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        values[section][field] = True
        return
    if raw in {"0", "false", "no", "off"}:
        values[section][field] = False
        return
    raise ConfigurationError(
        f"Invalid boolean for {env_name}: {environ[env_name]!r}. "
        "Use true/false, yes/no, on/off, or 1/0."
    )


def _validate(values: dict[str, dict[str, Any]]) -> None:
    errors: list[str] = []

    url = values["database"]["url"]
    if not isinstance(url, str) or not url.strip():
        errors.append(
            "database.url is required. "
            "Set JARVIS_DATABASE_URL or provide database.url in YAML. "
            "Run: set -a && source .env.jarvis && set +a"
        )
    elif not url.startswith(("postgresql://", "postgres://")):
        errors.append(
            "database.url must be a PostgreSQL URL "
            "(postgresql:// or postgres://)."
        )

    device = values["camera"]["device"]
    if not _is_int(device) or int(device) < 0:
        errors.append("camera.device must be an integer >= 0.")

    for field_name in ("width", "height", "fps"):
        value = values["camera"][field_name]
        if not _is_int(value) or int(value) <= 0:
            errors.append(
                f"camera.{field_name} must be an integer > 0."
            )

    source_name = values["camera"]["source_name"]
    if not isinstance(source_name, str) or not source_name.strip():
        errors.append("camera.source_name must be a non-empty string.")

    model_path = values["detector"]["model_path"]
    if not isinstance(model_path, str) or not model_path.strip():
        errors.append("detector.model_path must be a non-empty string.")

    confidence = values["detector"]["confidence_threshold"]
    if not _is_number(confidence) or not 0.0 <= float(confidence) <= 1.0:
        errors.append(
            "detector.confidence_threshold must be between 0.0 and 1.0."
        )

    timeout = values["detector"]["timeout_seconds"]
    if not _is_number(timeout) or float(timeout) <= 0:
        errors.append("detector.timeout_seconds must be a number > 0.")

    memory_source = values["memory"]["source"]
    if not isinstance(memory_source, str) or not memory_source.strip():
        errors.append("memory.source must be a non-empty string.")

    iou = values["memory"]["iou_threshold"]
    if not _is_number(iou) or not 0.0 <= float(iou) <= 1.0:
        errors.append(
            "memory.iou_threshold must be between 0.0 and 1.0."
        )

    max_missed = values["memory"]["max_missed_frames"]
    if not _is_int(max_missed) or int(max_missed) < 0:
        errors.append(
            "memory.max_missed_frames must be an integer >= 0."
        )

    identity_strategy = values["entity_memory"]["identity_strategy"]
    if (
        not isinstance(identity_strategy, str)
        or not identity_strategy.strip()
    ):
        errors.append(
            "entity_memory.identity_strategy must be a non-empty string."
        )
    elif identity_strategy.strip().lower() not in _VALID_IDENTITY_STRATEGIES:
        errors.append(
            "entity_memory.identity_strategy must be one of: "
            + ", ".join(sorted(_VALID_IDENTITY_STRATEGIES))
            + f". Got {identity_strategy!r}."
        )

    snapshot_interval = values["entity_memory"][
        "snapshot_min_interval_seconds"
    ]
    if not _is_number(snapshot_interval) or float(snapshot_interval) < 0:
        errors.append(
            "entity_memory.snapshot_min_interval_seconds must be "
            "a number >= 0."
        )

    snapshot_on_update = values["entity_memory"]["snapshot_on_update"]
    if not isinstance(snapshot_on_update, bool):
        errors.append(
            "entity_memory.snapshot_on_update must be a boolean."
        )

    api_enabled = values["api"]["enabled"]
    if not isinstance(api_enabled, bool):
        errors.append("api.enabled must be a boolean.")

    api_host = values["api"]["host"]
    if not isinstance(api_host, str) or not api_host.strip():
        errors.append("api.host must be a non-empty string.")

    api_port = values["api"]["port"]
    if not _is_int(api_port) or not 1 <= int(api_port) <= 65535:
        errors.append("api.port must be an integer between 1 and 65535.")

    api_default_limit = values["api"]["default_limit"]
    if not _is_int(api_default_limit) or int(api_default_limit) < 1:
        errors.append("api.default_limit must be an integer >= 1.")

    api_maximum_limit = values["api"]["maximum_limit"]
    if not _is_int(api_maximum_limit) or int(api_maximum_limit) < 1:
        errors.append("api.maximum_limit must be an integer >= 1.")
    elif (
        _is_int(api_default_limit)
        and int(api_default_limit) > int(api_maximum_limit)
    ):
        errors.append(
            "api.default_limit cannot exceed api.maximum_limit."
        )

    timeline_default_limit = values["timeline"]["default_limit"]
    if (
        not _is_int(timeline_default_limit)
        or int(timeline_default_limit) < 1
    ):
        errors.append(
            "timeline.default_limit must be an integer >= 1."
        )

    timeline_maximum_limit = values["timeline"]["maximum_limit"]
    if (
        not _is_int(timeline_maximum_limit)
        or int(timeline_maximum_limit) < 1
    ):
        errors.append(
            "timeline.maximum_limit must be an integer >= 1."
        )
    elif (
        _is_int(timeline_default_limit)
        and int(timeline_default_limit) > int(timeline_maximum_limit)
    ):
        errors.append(
            "timeline.default_limit cannot exceed timeline.maximum_limit."
        )

    activity_enabled = values["activity_stream"]["enabled"]
    if not isinstance(activity_enabled, bool):
        errors.append("activity_stream.enabled must be a boolean.")

    notify_channel = values["activity_stream"]["notify_channel"]
    if not isinstance(notify_channel, str) or not notify_channel.strip():
        errors.append(
            "activity_stream.notify_channel must be a non-empty string."
        )
    else:
        try:
            from storage.activity_notify import validate_notify_channel

            validate_notify_channel(notify_channel)
        except Exception as error:  # noqa: BLE001 - config surface
            errors.append(f"activity_stream.notify_channel: {error}")

    obs_enabled = values["activity_stream"][
        "observation_notifications_enabled"
    ]
    if not isinstance(obs_enabled, bool):
        errors.append(
            "activity_stream.observation_notifications_enabled "
            "must be a boolean."
        )

    obs_interval = values["activity_stream"][
        "observation_min_interval_seconds"
    ]
    if not _is_number(obs_interval) or float(obs_interval) < 0:
        errors.append(
            "activity_stream.observation_min_interval_seconds "
            "must be a number >= 0."
        )

    for field_name in (
        "client_queue_size",
        "max_connections",
    ):
        value = values["activity_stream"][field_name]
        if not _is_int(value) or int(value) < 1:
            errors.append(
                f"activity_stream.{field_name} must be an integer >= 1."
            )

    for field_name in (
        "heartbeat_interval_seconds",
        "reconnect_initial_seconds",
        "reconnect_max_seconds",
    ):
        value = values["activity_stream"][field_name]
        if not _is_number(value) or float(value) <= 0:
            errors.append(
                f"activity_stream.{field_name} must be a number > 0."
            )

    if (
        _is_number(values["activity_stream"]["reconnect_initial_seconds"])
        and _is_number(values["activity_stream"]["reconnect_max_seconds"])
        and float(values["activity_stream"]["reconnect_initial_seconds"])
        > float(values["activity_stream"]["reconnect_max_seconds"])
    ):
        errors.append(
            "activity_stream.reconnect_initial_seconds cannot exceed "
            "reconnect_max_seconds."
        )

    spatial_enabled = values["spatial"]["enabled"]
    if not isinstance(spatial_enabled, bool):
        errors.append("spatial.enabled must be a boolean.")

    position_strategy = values["spatial"]["position_strategy"]
    if (
        not isinstance(position_strategy, str)
        or not position_strategy.strip()
    ):
        errors.append(
            "spatial.position_strategy must be a non-empty string."
        )
    elif (
        position_strategy.strip().lower()
        not in _VALID_POSITION_STRATEGIES
    ):
        errors.append(
            "spatial.position_strategy must be one of: "
            + ", ".join(sorted(_VALID_POSITION_STRATEGIES))
            + f". Got {position_strategy!r}."
        )

    for field_name in (
        "enter_confirm_observations",
        "exit_confirm_observations",
        "maximum_zones_per_camera",
    ):
        value = values["spatial"][field_name]
        if not _is_int(value) or int(value) < 1:
            errors.append(
                f"spatial.{field_name} must be an integer >= 1."
            )

    for field_name in (
        "lost_track_timeout_seconds",
        "occupancy_stale_seconds",
    ):
        value = values["spatial"][field_name]
        if not _is_number(value) or float(value) <= 0:
            errors.append(
                f"spatial.{field_name} must be a number > 0."
            )
        elif float(value) > 3600:
            errors.append(
                f"spatial.{field_name} must be <= 3600 seconds."
            )

    max_zones = values["spatial"]["maximum_zones_per_camera"]
    if _is_int(max_zones) and int(max_zones) > 100:
        errors.append(
            "spatial.maximum_zones_per_camera must be <= 100."
        )

    publish_occ = values["spatial"]["publish_occupancy_changes"]
    if not isinstance(publish_occ, bool):
        errors.append(
            "spatial.publish_occupancy_changes must be a boolean."
        )

    alerts_enabled = values["alerts"]["enabled"]
    if not isinstance(alerts_enabled, bool):
        errors.append("alerts.enabled must be a boolean.")
    consumer_name = values["alerts"]["consumer_name"]
    if not isinstance(consumer_name, str) or not consumer_name.strip():
        errors.append("alerts.consumer_name must be a non-empty string.")
    for field_name in (
        "queue_size",
        "reconcile_batch_size",
        "max_rules",
        "default_cooldown_seconds",
        "max_metadata_bytes",
        "startup_catchup_limit",
    ):
        value = values["alerts"][field_name]
        if not _is_int(value) or int(value) < 1:
            errors.append(f"alerts.{field_name} must be an integer >= 1.")
    for field_name in (
        "reconcile_interval_seconds",
        "replay_overlap_seconds",
    ):
        value = values["alerts"][field_name]
        if not _is_number(value) or float(value) < 0:
            errors.append(f"alerts.{field_name} must be a number >= 0.")
    if _is_int(values["alerts"]["max_rules"]) and int(
        values["alerts"]["max_rules"]
    ) > 1000:
        errors.append("alerts.max_rules must be <= 1000.")
    tz_default = values["alerts"]["timezone_default"]
    if not isinstance(tz_default, str) or not tz_default.strip():
        errors.append("alerts.timezone_default must be a non-empty string.")
    else:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(str(tz_default).strip())
        except Exception:  # noqa: BLE001
            errors.append(
                "alerts.timezone_default must be a valid IANA timezone."
            )

    notif_enabled = values["notifications"]["enabled"]
    if not isinstance(notif_enabled, bool):
        errors.append("notifications.enabled must be a boolean.")
    worker_id = values["notifications"]["worker_id"]
    if not isinstance(worker_id, str) or not worker_id.strip():
        errors.append("notifications.worker_id must be a non-empty string.")
    allow_private = values["notifications"]["allow_private_targets"]
    if not isinstance(allow_private, bool):
        errors.append(
            "notifications.allow_private_targets must be a boolean."
        )
    for field_name in (
        "max_attempts",
        "max_concurrent_deliveries",
        "batch_size",
        "max_request_bytes",
        "max_response_bytes",
        "retention_days",
    ):
        value = values["notifications"][field_name]
        if not _is_int(value) or int(value) < 1:
            errors.append(
                f"notifications.{field_name} must be an integer >= 1."
            )
    if (
        _is_int(values["notifications"]["max_attempts"])
        and int(values["notifications"]["max_attempts"]) > 50
    ):
        errors.append("notifications.max_attempts must be <= 50.")
    if (
        _is_int(values["notifications"]["max_concurrent_deliveries"])
        and int(values["notifications"]["max_concurrent_deliveries"]) > 50
    ):
        errors.append(
            "notifications.max_concurrent_deliveries must be <= 50."
        )
    if (
        _is_int(values["notifications"]["batch_size"])
        and int(values["notifications"]["batch_size"]) > 500
    ):
        errors.append("notifications.batch_size must be <= 500.")
    if (
        _is_int(values["notifications"]["max_request_bytes"])
        and int(values["notifications"]["max_request_bytes"]) > 1_048_576
    ):
        errors.append(
            "notifications.max_request_bytes must be <= 1048576."
        )
    if (
        _is_int(values["notifications"]["max_response_bytes"])
        and int(values["notifications"]["max_response_bytes"]) > 1_048_576
    ):
        errors.append(
            "notifications.max_response_bytes must be <= 1048576."
        )
    if (
        _is_int(values["notifications"]["retention_days"])
        and int(values["notifications"]["retention_days"]) > 3650
    ):
        errors.append("notifications.retention_days must be <= 3650.")
    for field_name in (
        "worker_poll_interval_seconds",
        "initial_backoff_seconds",
        "max_backoff_seconds",
        "request_timeout_seconds",
        "lock_timeout_seconds",
    ):
        value = values["notifications"][field_name]
        if not _is_number(value) or float(value) <= 0:
            errors.append(
                f"notifications.{field_name} must be a number > 0."
            )
    backoff_mult = values["notifications"]["backoff_multiplier"]
    if not _is_number(backoff_mult) or float(backoff_mult) < 1.0:
        errors.append(
            "notifications.backoff_multiplier must be a number >= 1.0."
        )
    if (
        _is_number(values["notifications"]["initial_backoff_seconds"])
        and _is_number(values["notifications"]["max_backoff_seconds"])
        and float(values["notifications"]["initial_backoff_seconds"])
        > float(values["notifications"]["max_backoff_seconds"])
    ):
        errors.append(
            "notifications.initial_backoff_seconds cannot exceed "
            "max_backoff_seconds."
        )
    if (
        _is_number(values["notifications"]["worker_poll_interval_seconds"])
        and float(values["notifications"]["worker_poll_interval_seconds"])
        > 3600
    ):
        errors.append(
            "notifications.worker_poll_interval_seconds must be <= 3600."
        )
    if (
        _is_number(values["notifications"]["request_timeout_seconds"])
        and float(values["notifications"]["request_timeout_seconds"]) > 120
    ):
        errors.append(
            "notifications.request_timeout_seconds must be <= 120."
        )

    level = values["logging"]["level"]
    if not isinstance(level, str) or not level.strip():
        errors.append("logging.level must be a non-empty string.")
    elif level.strip().upper() not in _VALID_LOG_LEVELS:
        errors.append(
            "logging.level must be one of: "
            + ", ".join(sorted(_VALID_LOG_LEVELS))
            + f". Got {level!r}."
        )

    log_file = values["logging"]["log_file"]
    if not isinstance(log_file, str) or not log_file.strip():
        errors.append("logging.log_file must be a non-empty string.")

    platform = values["runtime"]["platform"]
    if not isinstance(platform, str) or not platform.strip():
        errors.append("runtime.platform must be a non-empty string.")

    application = values["runtime"]["application"]
    if not isinstance(application, str) or not application.strip():
        errors.append(
            "runtime.application must be a non-empty string."
        )

    if errors:
        raise ConfigurationError(
            "Invalid application configuration:\n- "
            + "\n- ".join(errors)
        )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _build_config(values: dict[str, dict[str, Any]]) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(url=str(values["database"]["url"]).strip()),
        camera=CameraConfig(
            device=int(values["camera"]["device"]),
            width=int(values["camera"]["width"]),
            height=int(values["camera"]["height"]),
            fps=int(values["camera"]["fps"]),
            source_name=str(values["camera"]["source_name"]).strip(),
        ),
        detector=DetectorConfig(
            model_path=str(values["detector"]["model_path"]).strip(),
            confidence_threshold=float(
                values["detector"]["confidence_threshold"]
            ),
            timeout_seconds=float(
                values["detector"]["timeout_seconds"]
            ),
        ),
        memory=MemoryConfig(
            source=str(values["memory"]["source"]).strip(),
            iou_threshold=float(values["memory"]["iou_threshold"]),
            max_missed_frames=int(
                values["memory"]["max_missed_frames"]
            ),
        ),
        entity_memory=EntityMemoryConfig(
            identity_strategy=str(
                values["entity_memory"]["identity_strategy"]
            )
            .strip()
            .lower(),
            snapshot_min_interval_seconds=float(
                values["entity_memory"]["snapshot_min_interval_seconds"]
            ),
            snapshot_on_update=bool(
                values["entity_memory"]["snapshot_on_update"]
            ),
        ),
        api=ApiConfig(
            enabled=bool(values["api"]["enabled"]),
            host=str(values["api"]["host"]).strip(),
            port=int(values["api"]["port"]),
            default_limit=int(values["api"]["default_limit"]),
            maximum_limit=int(values["api"]["maximum_limit"]),
        ),
        timeline=TimelineConfig(
            default_limit=int(values["timeline"]["default_limit"]),
            maximum_limit=int(values["timeline"]["maximum_limit"]),
        ),
        activity_stream=ActivityStreamConfig(
            enabled=bool(values["activity_stream"]["enabled"]),
            notify_channel=str(
                values["activity_stream"]["notify_channel"]
            ).strip(),
            observation_notifications_enabled=bool(
                values["activity_stream"][
                    "observation_notifications_enabled"
                ]
            ),
            observation_min_interval_seconds=float(
                values["activity_stream"][
                    "observation_min_interval_seconds"
                ]
            ),
            client_queue_size=int(
                values["activity_stream"]["client_queue_size"]
            ),
            heartbeat_interval_seconds=float(
                values["activity_stream"]["heartbeat_interval_seconds"]
            ),
            max_connections=int(
                values["activity_stream"]["max_connections"]
            ),
            reconnect_initial_seconds=float(
                values["activity_stream"]["reconnect_initial_seconds"]
            ),
            reconnect_max_seconds=float(
                values["activity_stream"]["reconnect_max_seconds"]
            ),
        ),
        spatial=SpatialConfig(
            enabled=bool(values["spatial"]["enabled"]),
            position_strategy=str(
                values["spatial"]["position_strategy"]
            )
            .strip()
            .lower(),
            enter_confirm_observations=int(
                values["spatial"]["enter_confirm_observations"]
            ),
            exit_confirm_observations=int(
                values["spatial"]["exit_confirm_observations"]
            ),
            lost_track_timeout_seconds=float(
                values["spatial"]["lost_track_timeout_seconds"]
            ),
            maximum_zones_per_camera=int(
                values["spatial"]["maximum_zones_per_camera"]
            ),
            occupancy_stale_seconds=float(
                values["spatial"]["occupancy_stale_seconds"]
            ),
            publish_occupancy_changes=bool(
                values["spatial"]["publish_occupancy_changes"]
            ),
        ),
        alerts=AlertsConfig(
            enabled=bool(values["alerts"]["enabled"]),
            consumer_name=str(values["alerts"]["consumer_name"]).strip(),
            queue_size=int(values["alerts"]["queue_size"]),
            reconcile_interval_seconds=float(
                values["alerts"]["reconcile_interval_seconds"]
            ),
            reconcile_batch_size=int(
                values["alerts"]["reconcile_batch_size"]
            ),
            replay_overlap_seconds=float(
                values["alerts"]["replay_overlap_seconds"]
            ),
            max_rules=int(values["alerts"]["max_rules"]),
            default_cooldown_seconds=int(
                values["alerts"]["default_cooldown_seconds"]
            ),
            max_metadata_bytes=int(values["alerts"]["max_metadata_bytes"]),
            startup_catchup_limit=int(
                values["alerts"]["startup_catchup_limit"]
            ),
            timezone_default=str(
                values["alerts"]["timezone_default"]
            ).strip(),
        ),
        notifications=NotificationsConfig(
            enabled=bool(values["notifications"]["enabled"]),
            worker_poll_interval_seconds=float(
                values["notifications"]["worker_poll_interval_seconds"]
            ),
            max_attempts=int(values["notifications"]["max_attempts"]),
            initial_backoff_seconds=float(
                values["notifications"]["initial_backoff_seconds"]
            ),
            max_backoff_seconds=float(
                values["notifications"]["max_backoff_seconds"]
            ),
            backoff_multiplier=float(
                values["notifications"]["backoff_multiplier"]
            ),
            request_timeout_seconds=float(
                values["notifications"]["request_timeout_seconds"]
            ),
            max_concurrent_deliveries=int(
                values["notifications"]["max_concurrent_deliveries"]
            ),
            batch_size=int(values["notifications"]["batch_size"]),
            lock_timeout_seconds=float(
                values["notifications"]["lock_timeout_seconds"]
            ),
            max_request_bytes=int(
                values["notifications"]["max_request_bytes"]
            ),
            max_response_bytes=int(
                values["notifications"]["max_response_bytes"]
            ),
            allow_private_targets=bool(
                values["notifications"]["allow_private_targets"]
            ),
            retention_days=int(values["notifications"]["retention_days"]),
            worker_id=str(values["notifications"]["worker_id"]).strip(),
        ),
        logging=LoggingConfig(
            level=str(values["logging"]["level"]).strip().upper(),
            log_file=str(values["logging"]["log_file"]).strip(),
        ),
        runtime=RuntimeConfig(
            platform=str(values["runtime"]["platform"]).strip(),
            application=str(values["runtime"]["application"]).strip(),
        ),
    )
