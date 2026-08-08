from pathlib import Path
from typing import Literal

import tomli as tomllib

from pydantic import BaseModel, Field


DEFAULT_HUB_CONFIG_PATH = Path("gattv.toml")
DEFAULT_CAMERA_CONFIG_PATH = Path("gattv.camera.toml")


class TelegramConfig(BaseModel):
    bot_token: str = Field(min_length=1)
    allowed_user_ids: set[int]
    notify_chat_ids: set[int] = Field(default_factory=set)


class CameraConfig(BaseModel):
    name: str = Field(min_length=1)
    index: int = 0
    width: int = Field(default=1280, gt=0)
    height: int = Field(default=720, gt=0)
    fps: int = Field(default=15, gt=0)
    rotation: Literal[0, 90, 180, 270] = 0
    warmup_frames: int = Field(default=15, ge=1)
    clip_seconds: int = Field(default=10, gt=0)


class MotionConfig(BaseModel):
    pre_seconds: int = Field(default=2, ge=0)
    post_seconds: int = Field(default=8, ge=0)
    cooldown_seconds: int = Field(default=60, ge=0)
    detection_fps: int = Field(default=5, gt=0)
    resize_width: int = Field(default=320, gt=0)
    sensitivity: int = Field(default=25, ge=1, le=255)
    changed_pixels: int = Field(default=150, gt=0)
    consecutive_frames: int = Field(default=2, gt=0)
    mode: Literal["notify", "clip"] = "notify"


class HubConfig(BaseModel):
    cameras: dict[str, str] = Field(default_factory=dict)
    default_camera: str | None = None
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=8765, ge=1, le=65535)


class HubServerConfig(BaseModel):
    telegram: TelegramConfig
    hub: HubConfig


class CameraServerConfig(BaseModel):
    camera: CameraConfig
    motion: MotionConfig = Field(default_factory=MotionConfig)
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=8766, ge=1, le=65535)
    hub_url: str = Field(min_length=1)


def _load_data(path: Path) -> dict[str, object]:
    with path.open("rb") as config_file:
        return tomllib.load(config_file)


def load_hub_config(path: Path = DEFAULT_HUB_CONFIG_PATH) -> HubServerConfig:
    return HubServerConfig.model_validate(_load_data(path))


def load_camera_config(path: Path = DEFAULT_CAMERA_CONFIG_PATH) -> CameraServerConfig:
    return CameraServerConfig.model_validate(_load_data(path))
