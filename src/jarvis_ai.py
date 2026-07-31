"""Main Jarvis Edge AI application."""

from __future__ import annotations

import logging
import time

import cv2

from camera import KinectCamera
from config import load_app_config
from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from detector import JarvisDetector
from display import WINDOW_NAME, draw_hud, save_screenshot
from rendering.tracked_renderer import render_tracked_objects
from core.identity import build_identity_matcher
from services.entity_memory_service import EntityMemoryService
from services.identity_history_service import IdentityHistoryService
from services.memory_service import MemoryService
from services.vision_persistence_service import (
    VisionPersistenceService,
)
from storage.config import DatabaseSettings
from storage.database import Database
from storage.entity_repository import EntityRepository
from storage.observation_repository import ObservationRepository
from storage.repository import VisionRepository
from storage.sqlalchemy_db import (
    create_entity_engine,
    create_session_factory,
)
from tracked_view import build_tracked_view
from utils import configure_logging
from vision_events import publish_frame_processed


def main() -> int:
    # Validate configuration before camera or Hailo startup.
    app_config = load_app_config()

    logger = configure_logging(app_config.logging.log_file)
    logger.setLevel(getattr(logging, app_config.logging.level))

    event_bus = EventBus()
    memory = MemoryService(
        event_bus,
        source=app_config.memory.source,
        iou_threshold=app_config.memory.iou_threshold,
        max_missed_frames=app_config.memory.max_missed_frames,
    )

    identity_history = IdentityHistoryService(event_bus)

    database_settings = DatabaseSettings(
        database_url=app_config.database.url,
    )
    database = Database(database_settings)
    vision_repository = VisionRepository(database)

    # Entity memory uses SQLAlchemy (Alembic-managed tables) alongside the
    # existing psycopg VisionRepository stack.
    entity_engine = create_entity_engine(app_config.database.url)
    entity_session_factory = create_session_factory(entity_engine)
    entity_repository = EntityRepository(entity_session_factory)
    observation_repository = ObservationRepository(entity_session_factory)

    vision_persistence = VisionPersistenceService(
        event_bus,
        vision_repository,
        camera_source=app_config.camera.source_name,
        metadata={
            "platform": app_config.runtime.platform,
            "application": app_config.runtime.application,
        },
        logger=logger,
    )

    entity_memory = EntityMemoryService(
        event_bus,
        entity_repository,
        observation_repository,
        session_factory=entity_session_factory,
        identity_matcher=build_identity_matcher(
            app_config.entity_memory.identity_strategy
        ),
        camera_id=app_config.camera.source_name,
        snapshot_min_interval_seconds=(
            app_config.entity_memory.snapshot_min_interval_seconds
        ),
        snapshot_on_update=app_config.entity_memory.snapshot_on_update,
        logger=logger,
    )

    camera = KinectCamera(
        device=app_config.camera.device,
        width=app_config.camera.width,
        height=app_config.camera.height,
        fps=app_config.camera.fps,
    )

    detector = JarvisDetector(
        model_path=app_config.detector.model_path,
        confidence_threshold=app_config.detector.confidence_threshold,
        timeout_seconds=app_config.detector.timeout_seconds,
    )

    frame_id = 0
    camera_opened = False
    memory_started = False
    identity_history_started = False
    vision_persistence_started = False
    entity_memory_started = False

    def log_object_entered(event: JarvisEvent) -> None:
        logger.info(
            "Object entered: %s confidence=%.3f",
            event.data["identity"],
            event.data["confidence"],
        )

    def log_object_exited(event: JarvisEvent) -> None:
        logger.info(
            "Object exited: %s frames_seen=%s",
            event.data["identity"],
            event.data["frames_seen"],
        )

    event_bus.subscribe(
        EventType.OBJECT_ENTERED,
        log_object_entered,
    )

    event_bus.subscribe(
        EventType.OBJECT_EXITED,
        log_object_exited,
    )

    try:
        logger.info("Starting Jarvis Edge AI")

        run_id = vision_persistence.start()
        vision_persistence_started = True

        logger.info(
            "Created persistent vision run: %s",
            run_id,
        )

        identity_history.start()
        identity_history_started = True

        entity_memory.start()
        entity_memory_started = True

        memory.start()
        memory_started = True

        event_bus.publish(
            JarvisEvent.create(
                EventType.SYSTEM_STARTED,
                source="jarvis",
                platform="raspberry_pi_5",
            )
        )

        logger.info("Opening Azure Kinect")
        camera.open()
        camera_opened = True

        event_bus.publish(
            JarvisEvent.create(
                EventType.CAMERA_OPENED,
                source="azure_kinect",
            )
        )

        detector.initialize()

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

        previous_time = time.perf_counter()
        smoothed_fps = 0.0

        while True:
            raw_frame = camera.read()
            _, detections = detector.detect(raw_frame)
            frame = raw_frame.copy()
            frame_id += 1

            current_time = time.perf_counter()
            elapsed = current_time - previous_time
            previous_time = current_time

            instantaneous_fps = (
                1.0 / elapsed
                if elapsed > 0
                else 0.0
            )

            if smoothed_fps == 0:
                smoothed_fps = instantaneous_fps
            else:
                smoothed_fps = (
                    0.9 * smoothed_fps
                    + 0.1 * instantaneous_fps
                )

            publish_frame_processed(
                event_bus,
                detections,
                frame_id=frame_id,
                source="azure_kinect",
                fps=smoothed_fps,
            )

            tracked_objects = build_tracked_view(
                memory.active_objects()
            )

            frame = render_tracked_objects(
                frame,
                tracked_objects,
            )

            frame = draw_hud(
                frame,
                fps=smoothed_fps,
                detector_status=detector.status,
            )

            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF

            if key in (ord("q"), 27):
                logger.info("Quit requested")
                break

            if key == ord("s"):
                path = save_screenshot(frame)

                event_bus.publish(
                    JarvisEvent.create(
                        EventType.SCREENSHOT_SAVED,
                        source="jarvis",
                        path=str(path),
                        frame_id=frame_id,
                    )
                )

                logger.info("Screenshot saved: %s", path)
                print(f"Screenshot saved: {path}")

        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0

    except Exception as error:
        event_bus.publish(
            JarvisEvent.create(
                EventType.SYSTEM_ERROR,
                source="jarvis",
                error_type=type(error).__name__,
                message=str(error),
            )
        )

        logger.exception("Jarvis encountered a fatal error")
        return 1

    finally:
        if camera_opened:
            event_bus.publish(
                JarvisEvent.create(
                    EventType.CAMERA_CLOSED,
                    source="azure_kinect",
                )
            )

        camera.release()

        try:
            detector.close()
        except Exception:
            logger.exception(
                "Failed to close Hailo detector cleanly"
            )

        if memory_started:
            memory.stop()

        if entity_memory_started:
            entity_memory.stop()

        if vision_persistence_started:
            vision_persistence.stop()

        if identity_history_started:
            histories = identity_history.all_histories()

            if histories:
                label_counts: dict[str, int] = {}

                for record in histories:
                    label = record["label"]
                    label_counts[label] = (
                        label_counts.get(label, 0) + 1
                    )

                counts_summary = ", ".join(
                    f"{count} {label}"
                    for label, count in sorted(
                        label_counts.items()
                    )
                )

                longest_observed = max(
                    histories,
                    key=lambda record: record[
                        "total_frames_seen"
                    ],
                )

                logger.info(
                    "Identity history: %d identities (%s)",
                    len(histories),
                    counts_summary,
                )
                logger.info(
                    "Longest observed: %s, %d frames",
                    longest_observed["identity"],
                    longest_observed["total_frames_seen"],
                )
            else:
                logger.info(
                    "Identity history: no identities observed"
                )

            identity_history.stop()

        event_bus.publish(
            JarvisEvent.create(
                EventType.SYSTEM_STOPPED,
                source="jarvis",
                frames_processed=frame_id,
            )
        )

        cv2.destroyAllWindows()
        logger.info(
            "Jarvis shutdown complete; processed %d frames",
            frame_id,
        )


if __name__ == "__main__":
    raise SystemExit(main())
