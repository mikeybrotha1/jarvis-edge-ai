# Jarvis Edge AI configuration

Typed application configuration is loaded at startup by `load_app_config()`.

## Precedence

Highest wins:

1. Environment variables (`JARVIS_*`)
2. YAML file (optional)
3. Built-in defaults (match current production behaviour)

## YAML file

- Example: `config/jarvis.example.yaml`
- Optional default path: `config/jarvis.yaml` (relative to the project root)
- Explicit path: set `JARVIS_CONFIG_PATH` (must exist if set)

```bash
cp config/jarvis.example.yaml config/jarvis.yaml
```

Unknown YAML sections or keys raise a configuration error.

## Environment variables

| Variable | Maps to |
|---|---|
| `JARVIS_DATABASE_URL` | `database.url` (required for `jarvis_ai`) |
| `JARVIS_CONFIG_PATH` | Path to YAML file |
| `JARVIS_CAMERA_DEVICE` | `camera.device` |
| `JARVIS_CAMERA_WIDTH` | `camera.width` |
| `JARVIS_CAMERA_HEIGHT` | `camera.height` |
| `JARVIS_CAMERA_FPS` | `camera.fps` |
| `JARVIS_CAMERA_SOURCE_NAME` | `camera.source_name` |
| `JARVIS_DETECTOR_MODEL_PATH` | `detector.model_path` |
| `JARVIS_DETECTOR_CONFIDENCE_THRESHOLD` | `detector.confidence_threshold` |
| `JARVIS_DETECTOR_TIMEOUT_SECONDS` | `detector.timeout_seconds` |
| `JARVIS_MEMORY_SOURCE` | `memory.source` |
| `JARVIS_MEMORY_IOU_THRESHOLD` | `memory.iou_threshold` |
| `JARVIS_MEMORY_MAX_MISSED_FRAMES` | `memory.max_missed_frames` |
| `JARVIS_ENTITY_MEMORY_IDENTITY_STRATEGY` | `entity_memory.identity_strategy` |
| `JARVIS_ENTITY_MEMORY_SNAPSHOT_MIN_INTERVAL_SECONDS` | `entity_memory.snapshot_min_interval_seconds` |
| `JARVIS_ENTITY_MEMORY_SNAPSHOT_ON_UPDATE` | `entity_memory.snapshot_on_update` |
| `JARVIS_API_ENABLED` | `api.enabled` |
| `JARVIS_API_HOST` | `api.host` |
| `JARVIS_API_PORT` | `api.port` |
| `JARVIS_API_DEFAULT_LIMIT` | `api.default_limit` |
| `JARVIS_API_MAXIMUM_LIMIT` | `api.maximum_limit` |
| `JARVIS_TIMELINE_DEFAULT_LIMIT` | `timeline.default_limit` |
| `JARVIS_TIMELINE_MAXIMUM_LIMIT` | `timeline.maximum_limit` |
| `JARVIS_ACTIVITY_STREAM_ENABLED` | `activity_stream.enabled` |
| `JARVIS_ACTIVITY_STREAM_NOTIFY_CHANNEL` | `activity_stream.notify_channel` |
| `JARVIS_ACTIVITY_STREAM_OBSERVATION_NOTIFICATIONS_ENABLED` | `activity_stream.observation_notifications_enabled` |
| `JARVIS_ACTIVITY_STREAM_OBSERVATION_MIN_INTERVAL_SECONDS` | `activity_stream.observation_min_interval_seconds` |
| `JARVIS_ACTIVITY_STREAM_CLIENT_QUEUE_SIZE` | `activity_stream.client_queue_size` |
| `JARVIS_ACTIVITY_STREAM_HEARTBEAT_INTERVAL_SECONDS` | `activity_stream.heartbeat_interval_seconds` |
| `JARVIS_ACTIVITY_STREAM_MAX_CONNECTIONS` | `activity_stream.max_connections` |
| `JARVIS_ACTIVITY_STREAM_RECONNECT_INITIAL_SECONDS` | `activity_stream.reconnect_initial_seconds` |
| `JARVIS_ACTIVITY_STREAM_RECONNECT_MAX_SECONDS` | `activity_stream.reconnect_max_seconds` |
| `JARVIS_SPATIAL_ENABLED` | `spatial.enabled` |
| `JARVIS_SPATIAL_POSITION_STRATEGY` | `spatial.position_strategy` |
| `JARVIS_SPATIAL_ENTER_CONFIRM_OBSERVATIONS` | `spatial.enter_confirm_observations` |
| `JARVIS_SPATIAL_EXIT_CONFIRM_OBSERVATIONS` | `spatial.exit_confirm_observations` |
| `JARVIS_SPATIAL_LOST_TRACK_TIMEOUT_SECONDS` | `spatial.lost_track_timeout_seconds` |
| `JARVIS_SPATIAL_MAXIMUM_ZONES_PER_CAMERA` | `spatial.maximum_zones_per_camera` |
| `JARVIS_SPATIAL_OCCUPANCY_STALE_SECONDS` | `spatial.occupancy_stale_seconds` |
| `JARVIS_SPATIAL_PUBLISH_OCCUPANCY_CHANGES` | `spatial.publish_occupancy_changes` |
| `JARVIS_ALERTS_ENABLED` | `alerts.enabled` |
| `JARVIS_ALERTS_CONSUMER_NAME` | `alerts.consumer_name` |
| `JARVIS_ALERTS_QUEUE_SIZE` | `alerts.queue_size` |
| `JARVIS_ALERTS_RECONCILE_INTERVAL_SECONDS` | `alerts.reconcile_interval_seconds` |
| `JARVIS_ALERTS_RECONCILE_BATCH_SIZE` | `alerts.reconcile_batch_size` |
| `JARVIS_ALERTS_REPLAY_OVERLAP_SECONDS` | `alerts.replay_overlap_seconds` |
| `JARVIS_ALERTS_MAX_RULES` | `alerts.max_rules` |
| `JARVIS_ALERTS_DEFAULT_COOLDOWN_SECONDS` | `alerts.default_cooldown_seconds` |
| `JARVIS_ALERTS_MAX_METADATA_BYTES` | `alerts.max_metadata_bytes` |
| `JARVIS_ALERTS_STARTUP_CATCHUP_LIMIT` | `alerts.startup_catchup_limit` |
| `JARVIS_ALERTS_TIMEZONE_DEFAULT` | `alerts.timezone_default` |
| `JARVIS_LOG_LEVEL` | `logging.level` |
| `JARVIS_LOG_FILE` | `logging.log_file` |
| `JARVIS_RUNTIME_PLATFORM` | `runtime.platform` |
| `JARVIS_RUNTIME_APPLICATION` | `runtime.application` |

## Database URL

A PostgreSQL URL is required for the primary application:

```bash
set -a
source .env.jarvis
set +a
```

`JARVIS_DATABASE_URL` always overrides YAML `database.url`.

Scripts that use `load_database_settings()` still read **only**
`JARVIS_DATABASE_URL` from the environment (not YAML).

## Built-in defaults

| Setting | Default |
|---|---|
| Camera | device `0`, `1280×720` @ `30` fps, source `azure_kinect` |
| Detector | YOLOv6n HEF under `/usr/local/hailo/...`, confidence `0.40`, timeout `10s` |
| Memory | source `vision_memory`, IoU `0.30`, max missed frames `8` |
| Entity memory | strategy `tracker_id`, snapshot interval `0.0` (no throttle), `snapshot_on_update` true |
| API | disabled, host `0.0.0.0`, port `8080`, default limit `50`, maximum limit `200` |
| Timeline | default limit `50`, maximum limit `200` |
| Activity stream | enabled, channel `jarvis_activity`, observations off, throttle `1.0s`, queue `100`, max connections `25` |
| Notifications | enabled, poll `1s`, max_attempts `5`, backoff `30s`×2.0 cap `1800s`, timeout `5s`, concurrency `3`, batch `50`, private targets off |
| Logging | level `INFO`, file `logs/jarvis.log` |
| Runtime | platform `raspberry_pi_5`, application `jarvis-edge-ai` |

### Notifications environment variables

| Variable | Config key |
|----------|------------|
| `JARVIS_NOTIFICATIONS_ENABLED` | `notifications.enabled` |
| `JARVIS_NOTIFICATIONS_WORKER_POLL_INTERVAL_SECONDS` | `notifications.worker_poll_interval_seconds` |
| `JARVIS_NOTIFICATIONS_MAX_ATTEMPTS` | `notifications.max_attempts` |
| `JARVIS_NOTIFICATIONS_INITIAL_BACKOFF_SECONDS` | `notifications.initial_backoff_seconds` |
| `JARVIS_NOTIFICATIONS_MAX_BACKOFF_SECONDS` | `notifications.max_backoff_seconds` |
| `JARVIS_NOTIFICATIONS_BACKOFF_MULTIPLIER` | `notifications.backoff_multiplier` |
| `JARVIS_NOTIFICATIONS_REQUEST_TIMEOUT_SECONDS` | `notifications.request_timeout_seconds` |
| `JARVIS_NOTIFICATIONS_MAX_CONCURRENT_DELIVERIES` | `notifications.max_concurrent_deliveries` |
| `JARVIS_NOTIFICATIONS_BATCH_SIZE` | `notifications.batch_size` |
| `JARVIS_NOTIFICATIONS_LOCK_TIMEOUT_SECONDS` | `notifications.lock_timeout_seconds` |
| `JARVIS_NOTIFICATIONS_MAX_REQUEST_BYTES` | `notifications.max_request_bytes` |
| `JARVIS_NOTIFICATIONS_MAX_RESPONSE_BYTES` | `notifications.max_response_bytes` |
| `JARVIS_NOTIFICATIONS_ALLOW_PRIVATE_TARGETS` | `notifications.allow_private_targets` |
| `JARVIS_NOTIFICATIONS_RETENTION_DAYS` | `notifications.retention_days` |
| `JARVIS_NOTIFICATIONS_WORKER_ID` | `notifications.worker_id` |
| `JARVIS_NOTIFICATIONS_ENCRYPTION_KEY` | Fernet key for signing-secret encryption (not YAML) |

See [outbound-notifications.md](outbound-notifications.md).

## Validation

Configuration is validated before camera or Hailo startup. Failures raise
`ConfigurationError` with field-specific messages.
