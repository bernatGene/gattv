from typer.testing import CliRunner
from unittest.mock import patch
from pathlib import Path

from gattv.cli import app, console
from gattv.config import CameraConfig, CameraServerConfig


runner = CliRunner()


def test_camera_uses_camera_config_default() -> None:
    result = runner.invoke(app, ["camera", "--help"])

    assert result.exit_code == 0
    assert "gattv.camera.toml" in result.stdout


def test_hub_uses_hub_config_default() -> None:
    result = runner.invoke(app, ["hub", "--help"])

    assert result.exit_code == 0
    assert "gattv.toml" in result.stdout
    assert "gattv.camera.toml" not in result.stdout


def test_init_camera_uses_camera_config_default() -> None:
    result = runner.invoke(app, ["config", "init-camera", "--help"])

    assert result.exit_code == 0
    assert "gattv.camera.toml" in result.stdout


def test_hardware_test_uses_camera_config_default() -> None:
    result = runner.invoke(app, ["test-hw", "--help"])

    assert result.exit_code == 0
    assert "gattv.camera.toml" in result.stdout
    assert "--seconds" in result.stdout
    assert "--censor" in result.stdout
    assert "report audio capture facts" in result.stdout


def test_hardware_test_forwards_av_options() -> None:
    config = CameraServerConfig(camera=CameraConfig(name="cat"), hub_url="http://hub")
    with (
        patch("gattv.cli._load_config", return_value=config),
        patch("gattv.cli.test_camera_hardware", return_value=True),
        patch("gattv.cli.run_av_experiment", return_value=True) as experiment,
    ):
        result = runner.invoke(
            app,
            [
                "test-hw",
                "--av-seconds",
                "180",
                "--audio-device",
                "default",
                "--output-path",
                "sync.mp4",
                "--censor",
            ],
        )

    assert result.exit_code == 0
    experiment.assert_called_once_with(
        config.camera, 10, 180, "default", Path("sync.mp4"), console, True
    )
