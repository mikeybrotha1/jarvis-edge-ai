#!/usr/bin/env python3

import cv2
import time
import datetime
import os


def main():
    print("=" * 40)
    print("Jarvis Edge Vision")
    print("=" * 40)
    print("Press S to save a screenshot")
    print("Press Q or ESC to quit")
    print("=" * 40)

    print("Opening Azure Kinect camera...")

    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)

    # Warm up camera
    for _ in range(10):
        cap.read()

    if not cap.isOpened():
        print("ERROR: Could not open camera.")
        return

    print("Camera opened successfully.")

    os.makedirs("screenshots", exist_ok=True)

    previous_time = time.perf_counter()
    smoothed_fps = 0.0

    while True:

        success, frame = cap.read()

        if not success or frame is None:
            print("Waiting for frame...")
            continue

        current_time = time.perf_counter()
        elapsed = current_time - previous_time
        previous_time = current_time

        fps = 1.0 / elapsed if elapsed > 0 else 0.0

        if smoothed_fps == 0:
            smoothed_fps = fps
        else:
            smoothed_fps = (0.9 * smoothed_fps) + (0.1 * fps)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cv2.putText(
            frame,
            "JARVIS EDGE VISION",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2,
        )

        cv2.putText(
            frame,
            timestamp,
            (20, 75),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )

        cv2.putText(
            frame,
            f"FPS: {smoothed_fps:.1f}",
            (20, 110),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )

        cv2.imshow("Jarvis Edge Vision", frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("s"):
            filename = datetime.datetime.now().strftime(
                "screenshots/%Y%m%d_%H%M%S.jpg"
            )
            cv2.imwrite(filename, frame)
            print(f"Saved {filename}")

        elif key == ord("q") or key == 27:
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
