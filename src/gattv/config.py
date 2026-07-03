from pathlib import Path
import tomllib

from pydantic import BaseModel, Field


DEFAULT_CONFIG_PATH = Path("gattv.toml")


class TelegramConfig(BaseModel):
    bot_token: str = Field(min_length=1)
    allowed_user_ids: set[int]


class Config(BaseModel):
    telegram: TelegramConfig


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    with path.open("rb") as config_file:
        data = tomllib.load(config_file)

    return Config.model_validate(data)
