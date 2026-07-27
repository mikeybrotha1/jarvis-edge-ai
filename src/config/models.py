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
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
