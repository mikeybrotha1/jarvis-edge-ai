"""Typed application configuration models for Jarvis Edge AI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """PostgreSQL connection settings."""

    url: str = ""


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """Azure Kinect / V4L2 camera settings."""

    device: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    source_name: str = "azure_kinect"


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """Hailo detector settings."""

    model_path: str = (
        "/usr/local/hailo/resources/models/hailo10h/yolov6n.hef"
    )
    confidence_threshold: float = 0.40
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    """Short-term object memory / identity tracking settings."""

    source: str = "vision_memory"
    iou_threshold: float = 0.30
    max_missed_frames: int = 8


@dataclass(frozen=True, slots=True)
class EntityMemoryConfig:
    """Persistent entity memory settings (v0.4.0).

    Placeholders
    ------------
    identity_strategy:
        Matcher used to build opaque identity keys. Default ``tracker_id``
        scopes keys by camera and tracker ID.
    snapshot_min_interval_seconds:
        Minimum seconds between intermediate (update) snapshots for the same
        entity. ``0.0`` disables throttling (snapshot every lifecycle event).
        Create and close snapshots are never throttled.
    snapshot_on_update:
        When False, intermediate update snapshots are skipped entirely
        (create/close still recorded). Reserved for future write-reduction;
        default True preserves full audit history.
    """

    identity_strategy: str = "tracker_id"
    snapshot_min_interval_seconds: float = 0.0
    snapshot_on_update: bool = True


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """Read-only HTTP entity query API settings (v0.4.1)."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    default_limit: int = 50
    maximum_limit: int = 200


@dataclass(frozen=True, slots=True)
class TimelineConfig:
    """Activity timeline query settings (v0.4.2)."""

    default_limit: int = 50
    maximum_limit: int = 200


@dataclass(frozen=True, slots=True)
class ActivityStreamConfig:
    """Real-time WebSocket activity stream settings (v0.5.0)."""

    enabled: bool = True
    notify_channel: str = "jarvis_activity"
    observation_notifications_enabled: bool = False
    observation_min_interval_seconds: float = 1.0
    client_queue_size: int = 100
    heartbeat_interval_seconds: float = 20.0
    max_connections: int = 25
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Application logging settings."""

    level: str = "INFO"
    log_file: str = "logs/jarvis.log"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Runtime metadata recorded with vision runs."""

    platform: str = "raspberry_pi_5"
    application: str = "jarvis-edge-ai"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Top-level Jarvis application configuration."""

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    entity_memory: EntityMemoryConfig = field(
        default_factory=EntityMemoryConfig
    )
    api: ApiConfig = field(default_factory=ApiConfig)
    timeline: TimelineConfig = field(default_factory=TimelineConfig)
    activity_stream: ActivityStreamConfig = field(
        default_factory=ActivityStreamConfig
    )
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
