from pathlib import Path
from typing import Literal

import tomli as tomllib

from pydantic import BaseModel, Field


DEFAULT_CONFIG_PATH = Path("gattv.toml")


class TelegramConfig(BaseModel):
    bot_token: str = Field(min_length=1)
    allowed_user_ids: set[int]


class CameraConfig(BaseModel):
    index: int = 0
    width: int = Field(default=1280, gt=0)
    height: int = Field(default=720, gt=0)
    fps: int = Field(default=15, gt=0)
    warmup_frames: int = Field(default=15, ge=1)
    clip_seconds: int = Field(default=10, gt=0)


class HubConfig(BaseModel):
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=8765, ge=1, le=65535)
    shared_token: str = Field(min_length=16)
    default_camera: str = Field(min_length=1)


class CameraTargetConfig(BaseModel):
    name: str = Field(min_length=1)
    url: str | None = None


class NodeConfig(BaseModel):
    name: str = Field(min_length=1)
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=8766, ge=1, le=65535)
    hub_url: str = Field(min_length=1)
    shared_token: str = Field(min_length=16)


class MotionConfig(BaseModel):
    pre_seconds: int = Field(default=5, ge=0)
    post_seconds: int = Field(default=5, ge=0)
    cooldown_seconds: int = Field(default=60, ge=0)
    detection_fps: int = Field(default=5, gt=0)
    resize_width: int = Field(default=320, gt=0)
    sensitivity: int = Field(default=25, ge=1, le=255)
    changed_pixels: int = Field(default=150, gt=0)
    consecutive_frames: int = Field(default=2, gt=0)
    mode: Literal["notify", "clip"] = "notify"


class Config(BaseModel):
    telegram: TelegramConfig | None = None
    hub: HubConfig | None = None
    cameras: list[CameraTargetConfig] = Field(default_factory=list)
    node: NodeConfig | None = None
    camera: CameraConfig = Field(default_factory=CameraConfig)
    motion: MotionConfig = Field(default_factory=MotionConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    with path.open("rb") as config_file:
        data = tomllib.load(config_file)

    return Config.model_validate(data)
