"""Run one Azure Kinect frame through Hailo-10H YOLOv6n."""

from __future__ import annotations

import threading
from typing import Any

import cv2
import numpy as np

from camera import KinectCamera
from hailo_inference import HailoInfer


MODEL_PATH = "/usr/local/hailo/resources/models/hailo10h/yolov6n.hef"
TIMEOUT_SECONDS = 15


def main() -> int:
    camera = KinectCamera(
        device=0,
        width=1280,
        height=720,
        fps=30,
    )

    inference = None
    finished = threading.Event()
    state: dict[str, Any] = {
        "output": None,
        "error": None,
    }

    try:
        print("Opening Azure Kinect...")
        camera.open()

        frame = camera.read()
        print("Camera frame:", frame.shape, frame.dtype)

        # Model input is 640 × 640 × 3.
        resized = cv2.resize(
            frame,
            (640, 640),
            interpolation=cv2.INTER_LINEAR,
        )

        # OpenCV captures BGR; YOLO expects RGB.
        input_tensor = cv2.cvtColor(
            resized,
            cv2.COLOR_BGR2RGB,
        )

        input_tensor = np.ascontiguousarray(
            input_tensor,
            dtype=np.uint8,
        )

        print("Prepared input:", input_tensor.shape, input_tensor.dtype)

        print("Configuring Hailo-10H...")
        inference = HailoInfer(
            MODEL_PATH,
            batch_size=1,
            input_type="UINT8",
            output_type="FLOAT32",
        )

        output_info = inference.get_vstream_info()[1][0]
        output_name = output_info.name

        print("Output name:", output_name)
        print("Launching asynchronous inference...")

        def inference_callback(
            completion_info,
            bindings_list,
        ) -> None:
            """Capture the output from the completed asynchronous job."""

            try:
                exception = getattr(completion_info, "exception", None)

                if exception is not None:
                    state["error"] = exception
                    return

                binding = bindings_list[0]

                # Explicitly select the model output.
                output = binding.output(output_name).get_buffer()

                # Hailo NMS output contains one variable-length array per class.
                # Preserve that structure instead of forcing it into one ndarray.
                state["output"] = [
                    np.array(class_detections, copy=True)
                    for class_detections in output
                ]

            except Exception as exc:
                state["error"] = exc

            finally:
                finished.set()

        inference.run(
            [input_tensor],
            inference_callback,
        )

        if not finished.wait(TIMEOUT_SECONDS):
            raise TimeoutError(
                f"Inference did not complete within {TIMEOUT_SECONDS} seconds"
            )

        if state["error"] is not None:
            raise RuntimeError(
                f"Hailo inference callback failed: {state['error']}"
            )

        output = state["output"]

        if output is None:
            raise RuntimeError("Inference completed without an output buffer")

        print()
        print("HAILO INFERENCE SUCCESS")
        print("Number of classes:", len(output))

        populated_classes = []
        total_detections = 0

        for class_id, class_detections in enumerate(output):
            count = len(class_detections)

            if count > 0:
                populated_classes.append(
                    (class_id, class_detections.shape, class_detections.dtype)
                )
                total_detections += count

        print("Total detections:", total_detections)
        print("Populated classes:")

        if populated_classes:
            for class_id, shape, dtype in populated_classes:
                print(
                    f"  class_id={class_id}, "
                    f"shape={shape}, dtype={dtype}"
                )
        else:
            print("  None detected in this frame")

        # Save the input frame used for this inference.
        output_path = "screenshots/hailo_async_input.jpg"

        if not cv2.imwrite(output_path, frame):
            raise RuntimeError(f"Could not save {output_path}")

        print("Input frame saved:", output_path)

        return 0

    finally:
        camera.release()

        if inference is not None:
            inference.close()

        print("Resources closed.")


if __name__ == "__main__":
    raise SystemExit(main())
