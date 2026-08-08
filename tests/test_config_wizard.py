from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from rich.console import Console
import typer

from gattv.config import load_camera_config, load_hub_config
from gattv.config_wizard import ConfigWizard
from gattv.setup import DiscoveredHub


def test_init_hub_writes_valid_config(tmp_path: Path) -> None:
    path = tmp_path / "hub.toml"
    wizard = ConfigWizard(Console())

    with (
        patch("gattv.config_wizard.local_ip", return_value="192.168.1.5"),
        patch(
            "gattv.config_wizard.typer.prompt",
            side_effect=["token", "20, 10", 9000],
        ),
    ):
        wizard.init_hub(path)

    config = load_hub_config(path)
    assert config.telegram.bot_token == "token"
    assert config.telegram.allowed_user_ids == {10, 20}
    assert config.hub.listen_port == 9000
    assert config.hub.cameras == {}


def test_init_hub_rejects_invalid_user_ids(tmp_path: Path) -> None:
    wizard = ConfigWizard(Console())

    with (
        patch("gattv.config_wizard.local_ip", return_value="192.168.1.5"),
        patch("gattv.config_wizard.typer.prompt", side_effect=["token", "abc"]),
        pytest.raises(typer.Exit),
    ):
        wizard.init_hub(tmp_path / "hub.toml")


def test_init_camera_writes_and_registers_config(tmp_path: Path) -> None:
    path = tmp_path / "camera.toml"
    wizard = ConfigWizard(Console())
    registration = AsyncMock()

    with (
        patch("gattv.config_wizard.local_ip", return_value="192.168.1.8"),
        patch("gattv.config_wizard.socket.gethostname", return_value="camera-host"),
        patch(
            "gattv.config_wizard.typer.prompt",
            side_effect=["kitchen", 2, 9001, "clip", "http://192.168.1.5:9000"],
        ),
        patch.object(wizard, "_suggest_hub_url", return_value="http://hub:8765"),
        patch("gattv.config_wizard.register_camera", registration),
    ):
        wizard.init_camera(path)

    config = load_camera_config(path)
    assert config.camera.name == "kitchen"
    assert config.camera.index == 2
    assert config.listen_port == 9001
    assert config.hub_url == "http://192.168.1.5:9000"
    assert config.motion.mode == "clip"
    sent = registration.await_args.args[1]
    assert sent.name == "kitchen"
    assert sent.url == "http://192.168.1.8:9001"


def test_suggest_hub_url_uses_selected_discovery() -> None:
    wizard = ConfigWizard(Console())
    hubs = [
        DiscoveredHub("one", "http://one:8765"),
        DiscoveredHub("two", "http://two:8765"),
    ]

    with (
        patch("gattv.config_wizard.discover_hubs", return_value=hubs),
        patch("gattv.config_wizard.typer.prompt", return_value=2),
    ):
        assert wizard._suggest_hub_url() == "http://two:8765"


def test_existing_config_requires_overwrite_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "gattv.toml"
    path.write_text("existing")

    with (
        patch("gattv.config_wizard.typer.confirm", return_value=False),
        pytest.raises(typer.Abort),
    ):
        ConfigWizard._confirm_overwrite(path)
