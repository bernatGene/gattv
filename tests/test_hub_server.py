from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer
import pytest
from rich.console import Console

from gattv.camera_server import CameraServer
from gattv.config import (
    CameraServerConfig,
    HubConfig,
    HubServerConfig,
    TelegramConfig,
    load_hub_config,
)
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
    hub.bot.notify_motion.return_value = 1

    async with TestClient(TestServer(hub.build_application())) as client:
        response = await client.post(
            "/motion/kitchen", data={"text": "Movement detected"}
        )

    assert response.status == 200
    hub.bot.notify_motion.assert_awaited_once_with("kitchen", "Movement detected")


@pytest.mark.asyncio
async def test_camera_video_upload_reaches_telegram_forwarding(
    tmp_path: Path, config: HubServerConfig
) -> None:
    config.hub.cameras["kitchen"] = "http://192.168.1.8:8766"
    config.hub.default_camera = "kitchen"
    hub = HubServer(config, tmp_path / "hub.toml", Console())
    received: list[bytes] = []

    async def send_motion_video(camera_name: str, path: Path) -> int:
        assert camera_name == "kitchen"
        received.append(path.read_bytes())
        return 1

    hub.bot.send_motion_video = send_motion_video
    async with TestClient(TestServer(hub.build_application())) as client:
        camera = CameraServer(
            CameraServerConfig.model_validate(
                {
                    "hub_url": str(client.make_url("/")),
                    "camera": {"name": "kitchen"},
                }
            )
        )
        video = tmp_path / "motion.mp4"
        video.write_bytes(b"motion-video")
        await camera._send_motion_video(video)

    assert received == [b"motion-video"]


@pytest.mark.asyncio
async def test_unknown_camera_motion_returns_not_found(
    tmp_path: Path, config: HubServerConfig
) -> None:
    hub = HubServer(config, tmp_path / "hub.toml", Console())

    async with TestClient(TestServer(hub.build_application())) as client:
        response = await client.post("/motion/unknown", data={"text": "motion"})

    assert response.status == 404


@pytest.mark.asyncio
async def test_run_uses_async_mdns_lifecycle(
    tmp_path: Path, config: HubServerConfig
) -> None:
    hub = HubServer(config, tmp_path / "hub.toml", Console())
    telegram = Mock()
    telegram.initialize = AsyncMock()
    telegram.start = AsyncMock()
    telegram.stop = AsyncMock()
    telegram.shutdown = AsyncMock()
    telegram.updater = Mock()
    telegram.updater.start_polling = AsyncMock()
    telegram.updater.stop = AsyncMock()
    hub.bot.build_application = Mock(return_value=telegram)
    zeroconf = Mock()
    zeroconf.async_register_service = AsyncMock()
    zeroconf.async_unregister_service = AsyncMock()

    with (
        patch("gattv.hub_server.web.AppRunner") as runner_class,
        patch("gattv.hub_server.web.TCPSite") as site_class,
        patch("gattv.hub_server.Zeroconf", return_value=zeroconf),
        patch("gattv.hub_server.local_ip", return_value="192.168.1.10"),
        patch("gattv.hub_server.start_caffeinate", return_value=None),
        patch("gattv.hub_server.stop_caffeinate"),
        patch("gattv.hub_server.asyncio.Event") as event_class,
    ):
        runner_class.return_value.setup = AsyncMock()
        runner_class.return_value.cleanup = AsyncMock()
        site_class.return_value.start = AsyncMock()
        event_class.return_value.wait = AsyncMock(side_effect=KeyboardInterrupt)
        with pytest.raises(KeyboardInterrupt):
            await hub.run()

    zeroconf.async_register_service.assert_awaited_once()
    zeroconf.async_unregister_service.assert_awaited_once()
    zeroconf.close.assert_called_once_with()
