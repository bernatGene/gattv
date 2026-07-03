from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2

from gattv.config import CameraConfig


class CameraError(Exception):
    pass


class CameraService:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config

    def capture_photo(self) -> Path:
        capture = cv2.VideoCapture(self.config.index)
        try:
            if not capture.isOpened():
                raise CameraError(f"Could not open camera index {self.config.index}.")

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            capture.set(cv2.CAP_PROP_FPS, self.config.fps)

            ok, frame = capture.read()
            if not ok:
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
