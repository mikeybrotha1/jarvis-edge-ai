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
| Logging | level `INFO`, file `logs/jarvis.log` |
| Runtime | platform `raspberry_pi_5`, application `jarvis-edge-ai` |

## Validation

Configuration is validated before camera or Hailo startup. Failures raise
`ConfigurationError` with field-specific messages.
