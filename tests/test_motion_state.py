from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gattv.config import MotionConfig
from gattv.motion import MotionService


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
    path = tmp_path / "motion.mp4"
    path.write_bytes(b"video")
    events: list[str] = []

    async def notify(text: str) -> None:
        events.append(f"notify:{text}")

    async def send_video(video_path: Path) -> None:
        assert video_path.read_bytes() == b"video"
        events.append("video")

    service = MotionService(
        Mock(),
        MotionConfig(mode="clip", cooldown_seconds=0),
        Mock(),
        notify,
        send_video,
    )

    def clips(camera, config, stop_requested, set_status, notify_detected):
        notify_detected()
        set_status("recording")
        yield path

    with patch("gattv.motion.motion_clips", clips):
        await service._run_clip_mode()

    assert events == ["notify:Motion detected. Recording video...", "video"]
    assert service.state.last_motion_at is not None
    assert not path.exists()
