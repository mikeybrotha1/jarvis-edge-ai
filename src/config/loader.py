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
    ApiConfig,
    AppConfig,
    CameraConfig,
    DatabaseConfig,
    DetectorConfig,
    EntityMemoryConfig,
    LoggingConfig,
    MemoryConfig,
    RuntimeConfig,
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
    "logging": frozenset({"level", "log_file"}),
    "runtime": frozenset({"platform", "application"}),
}

_VALID_IDENTITY_STRATEGIES = frozenset({"tracker_id"})

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
        logging=LoggingConfig(
            level=str(values["logging"]["level"]).strip().upper(),
            log_file=str(values["logging"]["log_file"]).strip(),
        ),
        runtime=RuntimeConfig(
            platform=str(values["runtime"]["platform"]).strip(),
            application=str(values["runtime"]["application"]).strip(),
        ),
    )
