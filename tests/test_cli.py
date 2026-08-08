from typer.testing import CliRunner

from gattv.cli import app


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
