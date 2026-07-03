import time
from collections.abc import Iterator
from dataclasses import dataclass

import cv2
import numpy as np

from gattv.camera import CameraError, CameraService
from gattv.config import MotionConfig


@dataclass(frozen=True)
class MotionSample:
    changed_pixels: int
    consecutive_frames: int
    detected: bool


def motion_samples(
    camera: CameraService, config: MotionConfig
) -> Iterator[MotionSample]:
    capture = camera._open_capture()
    try:
        frame = camera._warm_up(capture)
        previous = _prepare_frame(frame, config.resize_width)
        consecutive_frames = 0
        frame_interval = 1 / config.detection_fps
        next_frame_at = time.monotonic()

        while True:
            ok, frame = capture.read()
            if not ok:
                raise CameraError("Could not read a frame from the camera.")

            current = _prepare_frame(frame, config.resize_width)
            delta = cv2.absdiff(previous, current)
            threshold = cv2.threshold(
                delta, config.sensitivity, 255, cv2.THRESH_BINARY
            )[1]
            changed_pixels = cv2.countNonZero(threshold)

            if changed_pixels >= config.changed_pixels:
                consecutive_frames += 1
            else:
                consecutive_frames = 0

            previous = current
            yield MotionSample(
                changed_pixels=changed_pixels,
                consecutive_frames=consecutive_frames,
                detected=consecutive_frames >= config.consecutive_frames,
            )

            next_frame_at += frame_interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        capture.release()


def _prepare_frame(frame: np.ndarray, resize_width: int) -> np.ndarray:
    height, width = frame.shape[:2]
    resized_height = int(height * (resize_width / width))
    resized = cv2.resize(frame, (resize_width, resized_height))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (21, 21), 0)
