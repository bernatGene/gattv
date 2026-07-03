from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

import cv2

from gattv.config import CameraConfig


class CameraError(Exception):
    pass


class CameraService:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config

    def capture_photo(self) -> Path:
        capture = self._open_capture()
        try:
            if not capture.isOpened():
                raise CameraError(f"Could not open camera index {self.config.index}.")

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            capture.set(cv2.CAP_PROP_FPS, self.config.fps)

            frame = None
            for _ in range(self.config.warmup_frames):
                ok, frame = capture.read()
                if not ok:
                    raise CameraError("Could not read a frame from the camera.")

            if frame is None:
                raise CameraError("Could not read a frame from the camera.")

            with NamedTemporaryFile(
                prefix="gattv-photo-", suffix=".jpg", delete=False
            ) as file:
                path = Path(file.name)

            if not cv2.imwrite(str(path), frame):
                path.unlink(missing_ok=True)
                raise CameraError("Could not write camera frame to a photo file.")

            return path
        finally:
            capture.release()

    def _open_capture(self) -> cv2.VideoCapture:
        if sys.platform == "darwin":
            return cv2.VideoCapture(self.config.index, cv2.CAP_AVFOUNDATION)

        return cv2.VideoCapture(self.config.index)
