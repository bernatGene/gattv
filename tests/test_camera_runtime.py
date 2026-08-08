from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from rich.console import Console
from rich.panel import Panel

from gattv.camera_runtime import CameraRuntime, _format_timestamp, _sleep_status
from gattv.config import CameraServerConfig


def _config() -> CameraServerConfig:
    return CameraServerConfig.model_validate(
        {
            "listen_host": "0.0.0.0",
            "listen_port": 8766,
            "hub_url": "http://192.168.1.5:8765",
            "camera": {"name": "kitchen", "index": 1},
            "motion": {"mode": "clip"},
        }
    )


def test_status_panel_shows_camera_and_motion_state() -> None:
    runtime = CameraRuntime(_config(), Path("gattv.camera.toml"), Console())
    runtime.address = "192.168.1.8"
    runtime.server.motion.state.armed = True
    runtime.server.motion.state.status = "watching"
    runtime.server.motion.state.last_motion_at = datetime(2026, 8, 8, 12, 30, 10)

    text = Console(record=True, width=100)
    text.print(runtime.status_panel())
    rendered = text.export_text()

    assert "gattv camera: kitchen" in rendered
    assert "http://192.168.1.8:8766" in rendered
    assert "http://192.168.1.5:8765" in rendered
    assert "Recording" in rendered
    assert "index 1, 1280x720 @ 15 fps" in rendered
    assert "armed; watching" in rendered
    assert "clip" in rendered
    assert "2026-08-08 12:30:10" in rendered


def test_timestamp_format() -> None:
    assert _format_timestamp(None) == "-"
    assert _format_timestamp(datetime(2026, 8, 8, 12, 30, 10)) == "2026-08-08 12:30:10"


def test_sleep_status_shows_linux_inhibitor() -> None:
    with patch("gattv.camera_runtime.sys.platform", "linux"):
        assert _sleep_status() == "prevented while camera runs"


@pytest.mark.asyncio
async def test_run_passes_renderable_to_live_and_refreshes() -> None:
    runtime = CameraRuntime(_config(), Path("gattv.camera.toml"), Console())
    live = Mock()
    live.__enter__ = Mock(return_value=live)
    live.__exit__ = Mock(return_value=False)
    runtime.server.motion.state_changed.wait = AsyncMock(side_effect=KeyboardInterrupt)

    with (
        patch("gattv.camera_runtime.web.AppRunner") as runner_class,
        patch("gattv.camera_runtime.web.TCPSite") as site_class,
        patch("gattv.camera_runtime.Live", return_value=live) as live_class,
        patch("gattv.camera_runtime.start_caffeinate", return_value=None),
        patch("gattv.camera_runtime.start_systemd_inhibit", return_value=None),
        patch("gattv.camera_runtime.stop_caffeinate"),
        patch("gattv.camera_runtime.stop_systemd_inhibit"),
    ):
        runner_class.return_value.setup = AsyncMock()
        runner_class.return_value.cleanup = AsyncMock()
        site_class.return_value.start = AsyncMock()
        with pytest.raises(KeyboardInterrupt):
            await runtime.run()

    assert isinstance(live_class.call_args.args[0], Panel)
    live.update.assert_not_called()
