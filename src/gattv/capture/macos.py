from collections.abc import Iterator
from fractions import Fraction
import time

import av
import numpy as np

from gattv.camera import CameraError
from gattv.capture.models import CapturedUnit
from gattv.config import CameraConfig


class MacOsCaptureSource:
    def __init__(self, camera: CameraConfig) -> None:
        self._camera = camera
        self._container: av.container.InputContainer | None = None

    def units(self) -> Iterator[CapturedUnit]:
        try:
            self._container = av.open(
                f"{self._camera.index}:none",
                format="avfoundation",
                options={
                    "framerate": "30",
                    "pixel_format": "uyvy422",
                    "video_size": f"{self._camera.width}x{self._camera.height}",
                },
            )
            stream = self._container.streams.video[0]
            sequence = 0
            warmup_remaining = self._camera.warmup_frames
            for packet in self._container.demux(stream):
                if packet.size == 0:
                    continue
                if warmup_remaining:
                    warmup_remaining -= 1
                    continue

                codec_context = stream.codec_context
                pixel_format = (
                    codec_context.format.name
                    if codec_context.format is not None
                    else "uyvy422"
                )
                yield CapturedUnit(
                    sequence=sequence,
                    captured_at=time.monotonic(),
                    payload=bytes(packet),
                    codec="rawvideo",
                    pixel_format=pixel_format,
                    width=codec_context.width or self._camera.width,
                    height=codec_context.height or self._camera.height,
                    source_pts=packet.pts,
                    source_time_base=(
                        Fraction(packet.time_base)
                        if packet.time_base is not None
                        else None
                    ),
                )
                sequence += 1
        except av.FFmpegError as error:
            raise CameraError(f"Could not capture from the camera: {error}") from error

    def detection_image(self, unit: CapturedUnit) -> np.ndarray:
        expected_size = unit.width * unit.height * 2
        if len(unit.payload) != expected_size:
            raise CameraError(
                "Could not extract UYVY luminance from an unexpected frame size."
            )
        packed = np.frombuffer(unit.payload, dtype=np.uint8).reshape(
            unit.height, unit.width * 2
        )
        return packed[:, 1::2]

    def close(self) -> None:
        container = self._container
        self._container = None
        if container is not None:
            container.close()
