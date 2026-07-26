"""Main Jarvis Edge AI application."""

from __future__ import annotations

import time

import cv2

from camera import KinectCamera
from core.event_bus import EventBus
from core.events import EventType, JarvisEvent
from detector import JarvisDetector
from display import WINDOW_NAME, draw_hud, save_screenshot
from services.memory_service import MemoryService
from utils import configure_logging
from vision_events import publish_frame_processed


def main() -> int:
    logger = configure_logging()

    event_bus = EventBus()
    memory = MemoryService(
        event_bus,
        source="vision_memory",
        iou_threshold=0.30,
        max_missed_frames=8,
    )

    camera = KinectCamera(
        device=0,
        width=1280,
        height=720,
        fps=30,
    )

    detector = JarvisDetector()

    frame_id = 0
    camera_opened = False
    memory_started = False

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
            frame = camera.read()
            frame, detections = detector.detect(frame)
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
