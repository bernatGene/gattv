from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
import pytest
from rich.console import Console

from gattv.config import HubConfig, HubServerConfig, TelegramConfig, load_hub_config
from gattv.hub_server import HubServer


@pytest.fixture
def config() -> HubServerConfig:
    return HubServerConfig(
        telegram=TelegramConfig(bot_token="token", allowed_user_ids={1}),
        hub=HubConfig(),
    )


@pytest.mark.asyncio
async def test_approved_registration_updates_runtime_and_config(
    tmp_path: Path, config: HubServerConfig
) -> None:
    path = tmp_path / "hub.toml"
    hub = HubServer(config, path, Console())

    with patch("gattv.hub_server.typer.confirm", return_value=True):
        async with TestClient(TestServer(hub.build_application())) as client:
            response = await client.post(
                "/register",
                json={"name": "kitchen", "url": "http://192.168.1.8:8766"},
            )

    assert response.status == 200
    assert "kitchen" in hub.cameras
    assert hub.bot.default_camera == "kitchen"
    stored = load_hub_config(path)
    assert stored.hub.default_camera == "kitchen"
    assert stored.hub.cameras == {"kitchen": "http://192.168.1.8:8766"}


@pytest.mark.asyncio
async def test_rejected_registration_is_not_saved(
    tmp_path: Path, config: HubServerConfig
) -> None:
    path = tmp_path / "hub.toml"
    hub = HubServer(config, path, Console())

    with patch("gattv.hub_server.typer.confirm", return_value=False):
        async with TestClient(TestServer(hub.build_application())) as client:
            response = await client.post(
                "/register",
                json={"name": "kitchen", "url": "http://192.168.1.8:8766"},
            )

    assert response.status == 403
    assert hub.cameras == {}
    assert not path.exists()


@pytest.mark.asyncio
async def test_duplicate_registration_returns_conflict(
    tmp_path: Path, config: HubServerConfig
) -> None:
    config.hub.cameras["kitchen"] = "http://192.168.1.8:8766"
    hub = HubServer(config, tmp_path / "hub.toml", Console())

    async with TestClient(TestServer(hub.build_application())) as client:
        response = await client.post(
            "/register",
            json={"name": "kitchen", "url": "http://192.168.1.9:8766"},
        )

    assert response.status == 409


@pytest.mark.asyncio
async def test_motion_notification_routes_to_bot(
    tmp_path: Path, config: HubServerConfig
) -> None:
    config.hub.cameras["kitchen"] = "http://192.168.1.8:8766"
    config.hub.default_camera = "kitchen"
    hub = HubServer(config, tmp_path / "hub.toml", Console())
    hub.bot.notify_motion = AsyncMock()

    async with TestClient(TestServer(hub.build_application())) as client:
        response = await client.post(
            "/motion/kitchen", data={"text": "Movement detected"}
        )

    assert response.status == 200
    hub.bot.notify_motion.assert_awaited_once_with("kitchen", "Movement detected")


@pytest.mark.asyncio
async def test_unknown_camera_motion_returns_not_found(
    tmp_path: Path, config: HubServerConfig
) -> None:
    hub = HubServer(config, tmp_path / "hub.toml", Console())

    async with TestClient(TestServer(hub.build_application())) as client:
        response = await client.post("/motion/unknown", data={"text": "motion"})

    assert response.status == 404
