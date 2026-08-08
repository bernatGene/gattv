from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import numpy as np

from gattv.capture import CapturedUnit
from gattv.camera import CameraError
from gattv.config import CameraConfig, MotionConfig
from gattv.motion import MotionDetector, MotionService


def _service() -> MotionService:
    return MotionService(Mock(), MotionConfig(), Mock(), Mock(), Mock())


def test_state_change_sets_event() -> None:
    service = _service()

    service._update_state(armed=True, status="watching")

    assert service.state.armed is True
    assert service.state.status == "watching"
    assert service.state_changed.is_set()


def test_unchanged_state_does_not_set_event() -> None:
    service = _service()

    service._update_state(armed=False, status="stopped")

    assert not service.state_changed.is_set()


@pytest.mark.asyncio
async def test_clip_notifies_before_sending_video(tmp_path: Path) -> None:
    events: list[str] = []
    encoded_paths: list[Path] = []

    async def notify(text: str) -> None:
        events.append(f"notify:{text}")

    async def send_video(video_path: Path) -> None:
        assert video_path.read_bytes() == b"video"
        events.append("video")

    camera = Mock()
    camera.config = CameraConfig(name="cat", fps=2)
    service = MotionService(
        camera,
        MotionConfig(mode="clip", pre_seconds=0, post_seconds=0, cooldown_seconds=0),
        Mock(),
        notify,
        send_video,
    )
    service.state.armed = True

    class Source:
        def units(self) -> Iterator[CapturedUnit]:
            for sequence, captured_at in enumerate([0.0, 0.5]):
                yield CapturedUnit(
                    sequence,
                    captured_at,
                    bytes([sequence]),
                    "mjpeg",
                    "yuvj422p",
                    1,
                    1,
                )

        def detection_image(self, unit: CapturedUnit) -> np.ndarray:
            return np.zeros((1, 1), dtype=np.uint8)

        def close(self) -> None:
            pass

    def encode(clip, output_path: Path, fps: int) -> None:
        encoded_paths.append(output_path)
        output_path.write_bytes(b"video")

    with (
        patch("gattv.motion.create_capture_source", return_value=Source()),
        patch("gattv.motion.encode_clip", side_effect=encode),
        patch.object(MotionDetector, "detect", return_value=True),
    ):
        with pytest.raises(CameraError, match="stopped unexpectedly"):
            await service._run_clip_mode()

    assert events == ["notify:Motion detected. Recording video...", "video"]
    assert service.state.last_motion_at is not None
    assert len(encoded_paths) == 1
    assert not encoded_paths[0].exists()
