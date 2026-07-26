"""Main Jarvis Edge AI application."""

from __future__ import annotations

import time

import cv2

from camera import KinectCamera
from detector import JarvisDetector
from display import WINDOW_NAME, draw_hud, save_screenshot
from utils import configure_logging


def main() -> int:
    logger = configure_logging()

    camera = KinectCamera(
        device=0,
        width=1280,
        height=720,
        fps=30,
    )

    detector = JarvisDetector()

    try:
        logger.info("Starting Jarvis Edge AI")
        logger.info("Opening Azure Kinect")

        camera.open()
        detector.initialize()

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

        previous_time = time.perf_counter()
        smoothed_fps = 0.0

        while True:
            frame = camera.read()
            frame, detections = detector.detect(frame)

            current_time = time.perf_counter()
            elapsed = current_time - previous_time
            previous_time = current_time

            instantaneous_fps = 1.0 / elapsed if elapsed > 0 else 0.0

            if smoothed_fps == 0:
                smoothed_fps = instantaneous_fps
            else:
                smoothed_fps = (
                    0.9 * smoothed_fps
                    + 0.1 * instantaneous_fps
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
                logger.info("Screenshot saved: %s", path)
                print(f"Screenshot saved: {path}")

        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0

    except Exception:
        logger.exception("Jarvis encountered a fatal error")
        return 1

    finally:
        camera.release()

        try:
            detector.close()
        except Exception:
            logger.exception("Failed to close Hailo detector cleanly")

        cv2.destroyAllWindows()
        logger.info("Jarvis shutdown complete")


if __name__ == "__main__":
    raise SystemExit(main())
