"""Jarvis display and HUD functions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2


WINDOW_NAME = "Jarvis Edge AI"


def draw_hud(frame, fps: float, detector_status: str):
    """Draw the Jarvis status overlay."""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cv2.putText(
        frame,
        "JARVIS EDGE AI",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        frame,
        timestamp,
        (20, 68),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        frame,
        f"FPS: {fps:.1f}",
        (20, 98),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        frame,
        f"AI: {detector_status}",
        (20, 128),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        frame,
        "Q: Quit   S: Screenshot",
        (20, frame.shape[0] - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    return frame


def save_screenshot(frame, directory: str = "screenshots") -> Path:
    """Save the current frame as a timestamped screenshot."""

    output_directory = Path(directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    filename = datetime.now().strftime("jarvis_%Y%m%d_%H%M%S.jpg")
    output_path = output_directory / filename

    if not cv2.imwrite(str(output_path), frame):
        raise RuntimeError(f"Unable to save screenshot: {output_path}")

    return output_path
