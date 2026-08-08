import importlib
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import av
import cv2
import numpy as np

from gattv.capture.encoding import encode_clip
from gattv.capture.macos import MacOsCaptureSource, _open_camera, _packets
from gattv.capture.models import CapturedUnit, CompletedClip
from gattv.capture.source import create_capture_source
from gattv.config import CameraConfig


def test_capture_source_imports_only_current_platform_adapter() -> None:
    imported: list[str] = []

    def import_module(name: str):
        imported.append(name)
        module = Mock()
        module.LinuxCaptureSource.return_value = Mock()
        return module

    with (
        patch.object(sys, "platform", "linux"),
        patch.object(importlib, "import_module", side_effect=import_module),
    ):
        create_capture_source(CameraConfig(name="cat"))

    assert imported == ["gattv.capture.linux"]


def test_macos_source_extracts_uyvy_luminance() -> None:
    unit = CapturedUnit(
        sequence=0,
        captured_at=0.0,
        payload=bytes([10, 20, 30, 40, 50, 60, 70, 80]),
        codec="rawvideo",
        pixel_format="uyvy422",
        width=4,
        height=1,
    )

    image = MacOsCaptureSource(CameraConfig(name="cat")).detection_image(unit)

    np.testing.assert_array_equal(image, np.array([[20, 40, 60, 80]], dtype=np.uint8))


def test_macos_source_retries_transient_avfoundation_open() -> None:
    container = Mock()
    transient_error = av.BlockingIOError(35, "Resource temporarily unavailable", "0")

    with (
        patch(
            "gattv.capture.macos.av.open", side_effect=[transient_error, container]
        ) as open_camera,
        patch("gattv.capture.macos.time.sleep") as sleep,
    ):
        result = _open_camera(CameraConfig(name="cat", width=640, height=480))

    assert result is container
    assert open_camera.call_count == 2
    assert open_camera.call_args.args == ("0",)
    assert open_camera.call_args.kwargs == {
        "format": "avfoundation",
        "options": {"framerate": "30", "video_size": "640x480"},
    }
    sleep.assert_called_once_with(0.1)


def test_macos_source_retries_transient_avfoundation_demux() -> None:
    transient_error = av.BlockingIOError(35, "Resource temporarily unavailable", "0")
    packet = Mock()
    container = Mock()

    def blocked_packets():
        raise transient_error
        yield

    container.demux.side_effect = [blocked_packets(), iter([packet])]

    with patch("gattv.capture.macos.time.sleep") as sleep:
        packets = list(_packets(container, Mock()))

    assert packets == [packet]
    assert container.demux.call_count == 2
    sleep.assert_called_once_with(0.01)


def test_encodes_mjpeg_units_incrementally_to_h264(tmp_path: Path) -> None:
    units = []
    for index in range(3):
        image = np.full((16, 16, 3), index * 50, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        units.append(
            CapturedUnit(
                sequence=index,
                captured_at=index / 2,
                payload=encoded.tobytes(),
                codec="mjpeg",
                pixel_format="yuvj420p",
                width=16,
                height=16,
            )
        )
    clip = CompletedClip(0.5, 0.0, 1.0, tuple(units))
    output_path = tmp_path / "motion.mp4"

    encode_clip(clip, output_path, fps=2)

    with av.open(str(output_path)) as output:
        frames = list(output.decode(video=0))
        assert output.streams.video[0].codec_context.name == "h264"
    assert len(frames) == 3
