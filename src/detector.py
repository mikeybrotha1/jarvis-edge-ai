"""Object-detection interface for Jarvis Edge AI."""

from __future__ import annotations


class JarvisDetector:
    """
    Hailo detector placeholder.

    The camera and application architecture can run independently while the
    Hailo inference implementation is added and tested.
    """

    def __init__(self) -> None:
        self.ready = False
        self.status = "HAILO INTEGRATION PENDING"

    def initialize(self) -> None:
        """
        Initialise the detector.

        HailoRT model loading will be implemented in the next milestone.
        """
        self.ready = False

    def detect(self, frame):
        """Return the frame and an empty detection list for now."""

        return frame, []
