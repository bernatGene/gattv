from pathlib import Path
from unittest.mock import Mock

from gattv.bot import CatTvBot
from gattv.config import HubConfig, HubServerConfig, TelegramConfig, load_hub_config
from gattv.setup import write_hub_config


def test_motion_notifications_default_to_off() -> None:
    bot = CatTvBot(
        TelegramConfig(bot_token="token", allowed_user_ids={10, 20}), {}, None
    )

    assert bot.state.notify_chats == {}


def test_notification_choice_persists_across_bot_instances(tmp_path: Path) -> None:
    path = tmp_path / "hub.toml"
    config = HubServerConfig(
        telegram=TelegramConfig(bot_token="token", allowed_user_ids={10, 20}),
        hub=HubConfig(),
    )
    bot = CatTvBot(
        config.telegram,
        {},
        None,
        lambda: write_hub_config(path, config),
    )
    update = Mock()
    update.effective_chat.id = 20

    bot._set_chat_notify(update, True)

    reloaded = load_hub_config(path)
    assert reloaded.telegram.notify_chat_ids == {20}
    assert CatTvBot(reloaded.telegram, {}, None).state.notify_chats == {20: True}

    bot._set_chat_notify(update, False)

    assert load_hub_config(path).telegram.notify_chat_ids == set()
