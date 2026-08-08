import pytest

from gattv.camera import CameraError
from gattv.camera_client import CameraClient


@pytest.mark.asyncio
async def test_status_wraps_connection_errors() -> None:
    camera = CameraClient("Patio", "http://127.0.0.1:1")

    with pytest.raises(CameraError, match="Could not reach Patio"):
        await camera.status()
