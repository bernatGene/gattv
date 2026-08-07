from datetime import datetime
from pathlib import Path

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer
import pytest

from gattv.node_server import create_node_app
from gattv.nodes import CameraNodeStatus, RemoteCameraNode


TOKEN = "0123456789abcdef"


class FakeNode:
    name = "kitchen"
    clip_seconds = 10

    async def status(self) -> CameraNodeStatus:
        return CameraNodeStatus(True, "watching", datetime(2026, 8, 7, 12, 0))

    async def arm(self) -> bool:
        return True

    async def disarm(self) -> bool:
        return True

    async def capture_photo(self) -> Path:
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=".jpg", delete=False) as file:
            file.write(b"photo-data")
            return Path(file.name)

    async def record_clip(self) -> Path:
        raise AssertionError("not used")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_node_api_requires_bearer_token() -> None:
    async with TestClient(TestServer(create_node_app(FakeNode(), TOKEN))) as client:
        response = await client.get("/status")

    assert response.status == 401


@pytest.mark.asyncio
async def test_remote_node_reads_status_and_controls_motion() -> None:
    async with TestServer(create_node_app(FakeNode(), TOKEN)) as server:
        async with ClientSession() as session:
            node = RemoteCameraNode(
                "kitchen", str(server.make_url("/")), TOKEN, session, 10
            )

            status = await node.status()
            changed = await node.arm()

    assert status.armed is True
    assert status.motion_status == "watching"
    assert changed is True


@pytest.mark.asyncio
async def test_remote_node_downloads_media() -> None:
    async with TestServer(create_node_app(FakeNode(), TOKEN)) as server:
        async with ClientSession() as session:
            node = RemoteCameraNode(
                "kitchen", str(server.make_url("/")), TOKEN, session, 10
            )

            path = await node.capture_photo()

    try:
        assert path.read_bytes() == b"photo-data"
    finally:
        path.unlink(missing_ok=True)
