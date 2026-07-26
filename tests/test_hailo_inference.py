"""Single-frame HailoRT inference smoke test."""

from __future__ import annotations

import cv2
import numpy as np

from camera import KinectCamera
from hailo_platform import (
    ConfigureParams,
    FormatType,
    HEF,
    HailoStreamInterface,
    InferVStreams,
    InputVStreamParams,
    OutputVStreamParams,
    VDevice,
)


PY  raise SystemExit(main())lue:       {tensor}")nsor)}")ams):ork_group(rk group
(venv_hailo_apps) mikeybrotha@tradingbot:~/jarvis-edge-ai $ cd ~/hailo-apps
source setup_env.sh

cd ~/jarvis-edge-ai

PYTHONPATH="$PWD/src:$PYTHONPATH" \
python3 tests/test_hailo_inference.py
Setting up the environment...
Checking kernel version...
Project directory added to PYTHONPATH for this session:
/home/mikeybrotha/hailo-apps
Virtual environment 'venv_hailo_apps' activated
Environment variables loaded from /usr/local/hailo/resources/.env
Loading HEF...
Input:  yolov6n/input_layer1 (640, 640, 3)
Output: yolov6n/yolox_nms_postprocess (80, 5, 100)
Opening Azure Kinect...
Camera frame: (720, 1280, 3)
Prepared tensor: (1, 640, 640, 3) float32
Opening Hailo device...
Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/hailo_platform/pyhailort/pyhailort.py", line 3343, in configure
    configured_ngs_handles = self._vdevice.configure(hef._hef, configure_params_by_name)
hailo_platform.pyhailort._pyhailort.HailoRTStatusException: 7

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/home/mikeybrotha/jarvis-edge-ai/tests/test_hailo_inference.py", line 137, in <module>
    raise SystemExit(main())
                     ~~~~^^
  File "/home/mikeybrotha/jarvis-edge-ai/tests/test_hailo_inference.py", line 78, in main
    network_groups = device.configure(
        hef,
        configure_params,
    )
  File "/usr/lib/python3/dist-packages/hailo_platform/pyhailort/pyhailort.py", line 3342, in configure
    with ExceptionWrapper():
         ~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3/dist-packages/hailo_platform/pyhailort/pyhailort.py", line 109, in __exit__
    self._raise_indicative_status_exception(value)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
  File "/usr/lib/python3/dist-packages/hailo_platform/pyhailort/pyhailort.py", line 157, in _raise_indicative_status_exception
    raise self.create_exception_from_status(error_code) from libhailort_exception
hailo_platform.pyhailort.pyhailort.HailoRTException: libhailort failed with error: 7 (HAILO_NOT_IMPLEMENTED)
(venv_hailo_apps) mikeybrotha@tradingbot:~/jarvis-edge-ai $ python3 - <<'PY'
(venv_hailo_apps) mikeybrotha@tradingbot:~/jarvis-edge-ai $ python3 - <<'PY'
import inspect
from hailo_platform import (
from hailo_platform import (
    ConfigureParams,ms,
    InputVStreamParams,,
    OutputVStreamParams,
)
for obj, method in (
for obj, method in (, "create_from_hef"),
    (ConfigureParams, "create_from_hef"),rk_group"),
    (InputVStreamParams, "make_from_network_group"),,
    (OutputVStreamParams, "make_from_network_group"),
):  function = getattr(obj, method)
    function = getattr(obj, method)
    print(f"\n{obj.__name__}.{method}")
    print(f"\n{obj.__name__}.{method}")
    try:
    try:print(inspect.signature(function))
        print(inspect.signature(function))
    except Exception as exc:vailable:", exc)
        print("Signature unavailable:", exc)
    print(function.__doc__)
    print(function.__doc__)
