from pathlib import Path

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


class Config(BaseModel):
    telegram: TelegramConfig
    camera: CameraConfig = Field(default_factory=CameraConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    with path.open("rb") as config_file:
        data = tomllib.load(config_file)

    return Config.model_validate(data)
