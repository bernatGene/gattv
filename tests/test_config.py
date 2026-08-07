from pathlib import Path

from gattv.config import load_config


def test_loads_multicamera_hub_config(tmp_path: Path) -> None:
    path = tmp_path / "gattv.toml"
    path.write_text(
        """
[telegram]
bot_token = "token"
allowed_user_ids = [1]

[hub]
shared_token = "0123456789abcdef"
default_camera = "local"

[[cameras]]
name = "local"

[[cameras]]
name = "remote"
url = "http://camera:8766"
"""
    )

    config = load_config(path)

    assert config.hub is not None
    assert config.hub.default_camera == "local"
    assert [camera.name for camera in config.cameras] == ["local", "remote"]
