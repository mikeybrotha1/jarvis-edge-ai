"""Camera acquisition for Jarvis Edge AI."""

from __future__ import annotations

import cv2


class KinectCamera:
    """Open the Azure Kinect colour camera through V4L2."""

    def __init__(
        self,
        device: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
    ) -> None:
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.capture: cv2.VideoCapture | None = None

    def open(self) -> None:
        self.capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)

        self.capture.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(*"MJPG"),
        )
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.capture.set(cv2.CAP_PROP_FPS, self.fps)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.capture.isOpened():
            raise RuntimeError(
                f"Unable to open camera /dev/video{self.device}"
            )

    def read(self):
        if self.capture is None:
            raise RuntimeError("Camera has not been opened")

        success, frame = self.capture.read()

        if not success or frame is None:
            raise RuntimeError("Camera opened, but no frame was received")

        return frame

    def release(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None
