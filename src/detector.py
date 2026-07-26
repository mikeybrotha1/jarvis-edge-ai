"""Hailo-10H YOLOv6n object detector for Jarvis Edge AI."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from hailo_inference import HailoInfer


COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]


@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int


class JarvisDetector:
    """Run YOLOv6n inference on Hailo-10H and draw detections."""

    def __init__(
        self,
        model_path: str = (
            "/usr/local/hailo/resources/models/hailo10h/yolov6n.hef"
        ),
        confidence_threshold: float = 0.40,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.timeout_seconds = timeout_seconds
        self.input_width = 640
        self.input_height = 640

        self.inference = HailoInfer(
            model_path,
            batch_size=1,
            input_type="UINT8",
            output_type="FLOAT32",
        )

        output_infos = self.inference.get_vstream_info()[1]
        if not output_infos:
            raise RuntimeError("The HEF model exposes no output streams")

        self.output_name = output_infos[0].name
        self.status = "HAILO-10H ONLINE"

    def initialize(self) -> None:
        """Compatibility hook for the Jarvis application lifecycle.

        Hailo is already configured during object construction.
        """
        self.status = "HAILO-10H ONLINE"

    def detect(
        self,
        frame: np.ndarray,
    ) -> tuple[np.ndarray, list[Detection]]:
        """Run one inference and return the annotated frame and detections."""

        frame_height, frame_width = frame.shape[:2]

        resized = cv2.resize(
            frame,
            (self.input_width, self.input_height),
            interpolation=cv2.INTER_LINEAR,
        )

        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        input_tensor = np.ascontiguousarray(rgb, dtype=np.uint8)

        finished = threading.Event()
        state: dict[str, Any] = {
            "output": None,
            "error": None,
        }

        def callback(completion_info, bindings_list) -> None:
            try:
                exception = getattr(completion_info, "exception", None)
                if exception is not None:
                    state["error"] = exception
                    return

                raw_output = (
                    bindings_list[0]
                    .output(self.output_name)
                    .get_buffer()
                )

                state["output"] = [
                    np.array(class_detections, copy=True)
                    for class_detections in raw_output
                ]

            except Exception as exc:
                state["error"] = exc

            finally:
                finished.set()

        self.inference.run([input_tensor], callback)

        if not finished.wait(self.timeout_seconds):
            raise TimeoutError(
                f"Hailo inference exceeded {self.timeout_seconds} seconds"
            )

        if state["error"] is not None:
            raise RuntimeError(
                f"Hailo inference failed: {state['error']}"
            )

        output = state["output"]
        if output is None:
            raise RuntimeError("Hailo inference returned no output")

        detections = self._decode_output(
            output,
            frame_width,
            frame_height,
        )

        annotated = frame.copy()
        self._draw_detections(annotated, detections)

        self.status = (
            f"HAILO-10H ONLINE | OBJECTS: {len(detections)}"
        )

        return annotated, detections

    def _decode_output(
        self,
        output: list[np.ndarray],
        frame_width: int,
        frame_height: int,
    ) -> list[Detection]:
        detections: list[Detection] = []

        for class_id, class_rows in enumerate(output):
            if class_id >= len(COCO_LABELS):
                label = f"class_{class_id}"
            else:
                label = COCO_LABELS[class_id]

            for row in class_rows:
                values = np.asarray(row, dtype=np.float32).reshape(-1)

                if values.size < 5:
                    continue

                y_min, x_min, y_max, x_max, confidence = values[:5]

                confidence = float(confidence)
                if confidence < self.confidence_threshold:
                    continue

                # Hailo NMS coordinates are normally normalised 0–1.
                if max(abs(float(v)) for v in values[:4]) <= 2.0:
                    x1 = int(float(x_min) * frame_width)
                    y1 = int(float(y_min) * frame_height)
                    x2 = int(float(x_max) * frame_width)
                    y2 = int(float(y_max) * frame_height)
                else:
                    x1 = int(float(x_min) * frame_width / self.input_width)
                    y1 = int(float(y_min) * frame_height / self.input_height)
                    x2 = int(float(x_max) * frame_width / self.input_width)
                    y2 = int(float(y_max) * frame_height / self.input_height)

                x1 = max(0, min(frame_width - 1, x1))
                y1 = max(0, min(frame_height - 1, y1))
                x2 = max(0, min(frame_width - 1, x2))
                y2 = max(0, min(frame_height - 1, y2))

                if x2 <= x1 or y2 <= y1:
                    continue

                detections.append(
                    Detection(
                        class_id=class_id,
                        label=label,
                        confidence=confidence,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                    )
                )

        return detections

    @staticmethod
    def _draw_detections(
        frame: np.ndarray,
        detections: list[Detection],
    ) -> None:
        for detection in detections:
            cv2.rectangle(
                frame,
                (detection.x1, detection.y1),
                (detection.x2, detection.y2),
                (0, 255, 0),
                2,
            )

            text = (
                f"{detection.label} "
                f"{detection.confidence:.0%}"
            )

            text_size, baseline = cv2.getTextSize(
                text,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                2,
            )

            text_width, text_height = text_size
            text_y = max(text_height + 8, detection.y1)

            cv2.rectangle(
                frame,
                (detection.x1, text_y - text_height - 8),
                (
                    detection.x1 + text_width + 8,
                    text_y + baseline,
                ),
                (0, 255, 0),
                -1,
            )

            cv2.putText(
                frame,
                text,
                (detection.x1 + 4, text_y - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 0, 0),
                2,
                cv2.LINE_AA,
            )

    def close(self) -> None:
        """Release Hailo resources cleanly."""

        if self.inference is not None:
            self.inference.close()
            self.inference = None
