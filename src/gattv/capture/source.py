import importlib
import sys

from gattv.capture.worker import CaptureSource
from gattv.config import CameraConfig


def create_capture_source(camera: CameraConfig) -> CaptureSource:
    if sys.platform == "linux":
        module = importlib.import_module("gattv.capture.linux")
        return module.LinuxCaptureSource(camera)
    if sys.platform == "darwin":
        module = importlib.import_module("gattv.capture.macos")
        return module.MacOsCaptureSource(camera)
    raise RuntimeError(f"Continuous capture is not supported on {sys.platform}.")
