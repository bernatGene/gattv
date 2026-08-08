from pathlib import Path
from unittest.mock import Mock, patch

from zeroconf import IPVersion

from gattv.config import (
    CameraServerConfig,
    HubConfig,
    HubServerConfig,
    TelegramConfig,
    load_camera_config,
    load_hub_config,
)
from gattv.setup import SERVICE_TYPE, CameraRegistration, _HubListener
from gattv.setup import (
    hub_service_info,
    local_ip,
    write_camera_config,
    write_hub_config,
)


def test_hub_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "hub.toml"
    config = HubServerConfig(
        telegram=TelegramConfig(
            bot_token='a"b', allowed_user_ids={4, 2}, notify_chat_ids={8, -9}
        ),
        hub=HubConfig(
            default_camera="living room",
            cameras={"living room": "http://192.168.1.2:8766"},
        ),
    )

    write_hub_config(path, config)

    assert load_hub_config(path) == config
    assert not path.with_suffix(".toml.tmp").exists()


def test_empty_hub_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "hub.toml"
    config = HubServerConfig(
        telegram=TelegramConfig(bot_token="token", allowed_user_ids={1}),
        hub=HubConfig(),
    )

    write_hub_config(path, config)

    assert load_hub_config(path) == config


def test_camera_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "camera.toml"
    config = CameraServerConfig.model_validate(
        {
            "hub_url": "http://hub:8765",
            "camera": {"name": "kitchen", "rotation": 270},
        }
    )

    write_camera_config(path, config)

    assert load_camera_config(path) == config


def test_local_ip_returns_private_socket_address() -> None:
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.getsockname.return_value = ("192.168.1.20", 1234)

    with patch("gattv.setup.socket.socket", return_value=connection):
        assert local_ip() == "192.168.1.20"


def test_local_ip_falls_back_for_public_address() -> None:
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.getsockname.return_value = ("8.8.8.8", 1234)

    with patch("gattv.setup.socket.socket", return_value=connection):
        assert local_ip() == "127.0.0.1"


def test_hub_listener_builds_url() -> None:
    info = Mock(port=8765)
    info.parsed_scoped_addresses.return_value = ["192.168.1.10"]
    zeroconf = Mock()
    zeroconf.get_service_info.return_value = info
    listener = _HubListener()

    listener.add_service(zeroconf, SERVICE_TYPE, f"Home.{SERVICE_TYPE}")

    assert listener.hubs[f"Home.{SERVICE_TYPE}"].name == "Home"
    assert listener.hubs[f"Home.{SERVICE_TYPE}"].url == "http://192.168.1.10:8765"
    info.parsed_scoped_addresses.assert_called_once_with(IPVersion.V4Only)


def test_hub_service_info_uses_address_and_port() -> None:
    info = hub_service_info("192.168.1.10", 9000)

    assert info.parsed_addresses() == ["192.168.1.10"]
    assert info.port == 9000
    assert info.type == SERVICE_TYPE


def test_registration_rejects_non_http_url() -> None:
    try:
        CameraRegistration(name="cam", url="ftp://camera")
    except ValueError:
        pass
    else:
        raise AssertionError("non-HTTP camera URL was accepted")
