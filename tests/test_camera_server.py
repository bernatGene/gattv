from pathlib import Path
from unittest.mock import AsyncMock, Mock

from aiohttp.test_utils import TestClient, TestServer
import pytest

from gattv.camera_server import CameraServer
from gattv.config import CameraServerConfig


def _config() -> CameraServerConfig:
    return CameraServerConfig.model_validate(
        {"hub_url": "http://hub:8765", "camera": {"name": "kitchen"}}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "method", "suffix", "content_type", "content"),
    [
        ("photo", "capture_photo", ".jpg", "image/jpeg", b"jpeg-data"),
        ("video", "record_clip", ".mp4", "video/mp4", b"mp4-data"),
    ],
)
async def test_capture_streams_before_deleting_temporary_file(
    tmp_path: Path,
    endpoint: str,
    method: str,
    suffix: str,
    content_type: str,
    content: bytes,
) -> None:
    path = tmp_path / f"capture{suffix}"
    path.write_bytes(content)
    server = CameraServer(_config())
    setattr(server.camera, method, Mock(return_value=path))

    async with TestClient(TestServer(server.build_application())) as client:
        response = await client.post(f"/{endpoint}")
        body = await response.read()

    assert response.status == 200
    assert response.content_type == content_type
    assert body == content
    assert response.content_length == len(content)
    assert not path.exists()


@pytest.mark.asyncio
async def test_capture_resumes_armed_motion_after_streaming(tmp_path: Path) -> None:
    path = tmp_path / "capture.jpg"
    path.write_bytes(b"jpeg-data")
    server = CameraServer(_config())
    server.camera.capture_photo = Mock(return_value=path)
    server.motion.state.armed = True
    server.motion.pause = AsyncMock(return_value=True)
    server.motion.resume = Mock()

    async with TestClient(TestServer(server.build_application())) as client:
        response = await client.post("/photo")
        await response.read()

    assert response.status == 200
    server.motion.resume.assert_called_once_with()
