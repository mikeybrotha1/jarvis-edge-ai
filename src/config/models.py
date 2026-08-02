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
class SpatialConfig:
    """Spatial intelligence / zone matching settings (v0.6.0)."""

    enabled: bool = True
    position_strategy: str = "bottom_center"
    enter_confirm_observations: int = 3
    exit_confirm_observations: int = 3
    lost_track_timeout_seconds: float = 15.0
    maximum_zones_per_camera: int = 10
    occupancy_stale_seconds: float = 60.0
    publish_occupancy_changes: bool = True


@dataclass(frozen=True, slots=True)
class AlertsConfig:
    """Durable alerts & rule evaluation (v0.8.0)."""

    enabled: bool = True
    consumer_name: str = "jarvis-alert-evaluator"
    queue_size: int = 500
    reconcile_interval_seconds: float = 2.0
    reconcile_batch_size: int = 100
    replay_overlap_seconds: float = 5.0
    max_rules: int = 100
    default_cooldown_seconds: int = 60
    max_metadata_bytes: int = 8192
    startup_catchup_limit: int = 500
    timezone_default: str = "UTC"


@dataclass(frozen=True, slots=True)
class NotificationsConfig:
    """Outbound webhook notification delivery (v0.9.0)."""

    enabled: bool = True
    worker_poll_interval_seconds: float = 1.0
    max_attempts: int = 5
    initial_backoff_seconds: float = 30.0
    max_backoff_seconds: float = 1800.0
    backoff_multiplier: float = 2.0
    request_timeout_seconds: float = 5.0
    max_concurrent_deliveries: int = 3
    batch_size: int = 50
    lock_timeout_seconds: float = 60.0
    max_request_bytes: int = 65536
    max_response_bytes: int = 8192
    allow_private_targets: bool = False
    retention_days: int = 30
    worker_id: str = "jarvis-notification-worker"


@dataclass(frozen=True, slots=True)
class ObservationsRetentionPolicy:
    """Observation row retention (v0.10.0 phase 3 — policy only).

    Eligibility (not executed in phase 3): rows older than ``keep_days`` that
    remain referentially safe with foreign keys. Does not delete active
    checkpoint or recovery data.
    """

    enabled: bool = False
    keep_days: int = 30


@dataclass(frozen=True, slots=True)
class EntitiesRetentionPolicy:
    """Entity aggregate retention (**experimental** in v0.10.0).

    Default remains ``enabled=False``. Deleting an entity triggers database
    ``ON DELETE CASCADE`` for observations, snapshots, zone sessions, alerts,
    and evaluator state (deliveries cascade via alerts). Eligibility is
    narrowed so entities with any remaining alert or evaluator rows are never
    selected; prune those domains first. Active entities and entities with
    open zone sessions are never deleted.
    """

    enabled: bool = False
    keep_closed_days: int = 90


@dataclass(frozen=True, slots=True)
class ZoneSessionsRetentionPolicy:
    """Entity-zone session retention.

    Eligibility: only **closed** sessions older than ``keep_closed_days``.
    Open dwell sessions must never be deleted.
    """

    enabled: bool = False
    keep_closed_days: int = 90


@dataclass(frozen=True, slots=True)
class AlertsRetentionPolicy:
    """Alert row retention.

    Eligibility: only **resolved** terminal alerts older than
    ``keep_resolved_days``. Open and acknowledged (non-terminal) alerts must
    never be deleted.
    """

    enabled: bool = False
    keep_resolved_days: int = 90


@dataclass(frozen=True, slots=True)
class EvaluatorStateRetentionPolicy:
    """Alert evaluator state retention.

    Eligibility: only inactive/cleared evaluator state older than
    ``keep_inactive_days``. Pending or triggered active conditions must never
    be deleted. Checkpoint rows are out of scope.
    """

    enabled: bool = False
    keep_inactive_days: int = 30


@dataclass(frozen=True, slots=True)
class NotificationDeliveriesRetentionPolicy:
    """Notification delivery / attempt retention.

    Eligibility: only terminal deliveries (``delivered`` / ``exhausted``)
    older than ``keep_terminal_days``, plus related attempt history.
    Pending/processing/failed (retry scheduled) must never be deleted.
    """

    enabled: bool = False
    keep_terminal_days: int = 90


@dataclass(frozen=True, slots=True)
class RetentionConfig:
    """Data lifecycle retention policy (v0.10.0).

    Phase 3 ships **configuration only**. No worker, deletion engine, or
    manual-run API is started from this model.

    Defaults are safe for upgrade:
    - global ``enabled=False``
    - ``dry_run=True``
    - every domain ``enabled=False``
    - conservative keep periods

    Planned execution model (phase 4+): dry-run first, fixed small batches,
    one short transaction per batch, restart-safe, isolated from API/vision
    paths. Checkpoint/recovery tables are not eligible for cleanup unless a
    later design explicitly proves safety.
    """

    enabled: bool = False
    dry_run: bool = True
    interval_seconds: int = 86400
    batch_size: int = 250
    max_batches_per_run: int = 4
    # Phase 5: explicit guard for POST /api/v1/ops/retention/run (default off).
    allow_manual_destructive_run: bool = False
    observations: ObservationsRetentionPolicy = field(
        default_factory=ObservationsRetentionPolicy
    )
    entities: EntitiesRetentionPolicy = field(
        default_factory=EntitiesRetentionPolicy
    )
    zone_sessions: ZoneSessionsRetentionPolicy = field(
        default_factory=ZoneSessionsRetentionPolicy
    )
    alerts: AlertsRetentionPolicy = field(
        default_factory=AlertsRetentionPolicy
    )
    evaluator_state: EvaluatorStateRetentionPolicy = field(
        default_factory=EvaluatorStateRetentionPolicy
    )
    notification_deliveries: NotificationDeliveriesRetentionPolicy = field(
        default_factory=NotificationDeliveriesRetentionPolicy
    )

    def any_domain_enabled(self) -> bool:
        """True if at least one domain policy is enabled."""

        return any(
            (
                self.observations.enabled,
                self.entities.enabled,
                self.zone_sessions.enabled,
                self.alerts.enabled,
                self.evaluator_state.enabled,
                self.notification_deliveries.enabled,
            )
        )


@dataclass(frozen=True, slots=True)
class OpsConfig:
    """Operational observability and data lifecycle (v0.10.0)."""

    retention: RetentionConfig = field(default_factory=RetentionConfig)


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
    spatial: SpatialConfig = field(default_factory=SpatialConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    notifications: NotificationsConfig = field(
        default_factory=NotificationsConfig
    )
    ops: OpsConfig = field(default_factory=OpsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
