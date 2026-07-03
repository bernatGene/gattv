from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time

import cv2
import imageio_ffmpeg
import numpy as np

from gattv.config import CameraConfig


class CameraError(Exception):
    pass


class CameraService:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config

    def capture_photo(self) -> Path:
        capture = self._open_capture()
        try:
            frame = self._warm_up(capture)

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

    def record_clip(self, seconds: int | None = None) -> Path:
        duration = seconds or self.config.clip_seconds
        capture = self._open_capture()
        writer = None
        raw_path = None
        output_path = None
        try:
            frame = self._warm_up(capture)
            height, width = frame.shape[:2]

            with NamedTemporaryFile(
                prefix="gattv-video-raw-", suffix=".avi", delete=False
            ) as file:
                raw_path = Path(file.name)

            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            writer = cv2.VideoWriter(
                str(raw_path), fourcc, self.config.fps, (width, height)
            )
            if not writer.isOpened():
                raise CameraError("Could not open video writer.")

            frame_interval = 1 / self.config.fps
            next_frame_at = time.monotonic()
            end_at = next_frame_at + duration

            while time.monotonic() < end_at:
                ok, frame = capture.read()
                if not ok:
                    raise CameraError("Could not read a frame from the camera.")

                writer.write(frame)
                next_frame_at += frame_interval
                sleep_for = next_frame_at - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)

            writer.release()
            writer = None

            with NamedTemporaryFile(
                prefix="gattv-video-", suffix=".mp4", delete=False
            ) as file:
                output_path = Path(file.name)

            self._encode_mp4(raw_path, output_path)
            return output_path
        except Exception:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            raise
        finally:
            if writer is not None:
                writer.release()
            capture.release()
            if raw_path is not None:
                raw_path.unlink(missing_ok=True)

    def _encode_mp4(self, input_path: Path, output_path: Path) -> None:
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(input_path),
            "-an",
            "-vcodec",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise CameraError(f"Could not encode MP4: {result.stderr.strip()}")

    def _warm_up(self, capture: cv2.VideoCapture) -> np.ndarray:
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

        return frame

    def _open_capture(self) -> cv2.VideoCapture:
        if sys.platform == "darwin":
            return cv2.VideoCapture(self.config.index, cv2.CAP_AVFOUNDATION)

        return cv2.VideoCapture(self.config.index)
