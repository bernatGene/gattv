from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from gattv.bot import CatTvBot
from gattv.camera import CameraError
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


@pytest.mark.asyncio
async def test_status_reports_unavailable_cameras_independently() -> None:
    kitchen = _camera(
        "Kitchen",
        {"armed": True, "motion": "watching", "last_motion_at": None},
    )
    patio = _camera("Patio", CameraError("host unreachable"))
    broken = _camera("Broken", {"armed": "yes", "motion": "watching"})
    bot = CatTvBot(
        TelegramConfig(bot_token="token", allowed_user_ids={10}),
        {"Kitchen": kitchen, "Patio": patio, "Broken": broken},
        "Kitchen",
    )
    update = _command_update()

    await bot.status(update, Mock())

    text = update.effective_message.reply_text.await_args.kwargs["text"]
    assert "Cameras: 1 armed, 0 disarmed, 2 unavailable" in text
    assert "Kitchen: Armed; motion: watching" in text
    assert "Patio: Unavailable" in text
    assert "Broken: Unavailable" in text


@pytest.mark.asyncio
async def test_camera_callback_arms_only_target_camera() -> None:
    kitchen = _camera(
        "Kitchen",
        {"armed": True, "motion": "watching", "last_motion_at": None},
    )
    patio = _camera(
        "Patio",
        {"armed": True, "motion": "watching", "last_motion_at": None},
    )
    bot = CatTvBot(
        TelegramConfig(bot_token="token", allowed_user_ids={10}),
        {"Kitchen": kitchen, "Patio": patio},
        "Kitchen",
    )
    callback_id = bot._callback_id_for("Patio")
    update = _callback_update(f"gattv:arm:{callback_id}")

    await bot.camera_callback(update, Mock())

    patio.arm.assert_awaited_once_with()
    kitchen.arm.assert_not_awaited()
    update.callback_query.answer.assert_awaited_once_with()
    text = update.callback_query.edit_message_text.await_args.kwargs["text"]
    assert "Patio\nState: Armed" in text


@pytest.mark.asyncio
async def test_bulk_arm_names_successes_and_unavailable_cameras() -> None:
    kitchen = _camera(
        "Kitchen",
        {"armed": False, "motion": "stopped", "last_motion_at": None},
    )
    patio = _camera(
        "Patio",
        {"armed": False, "motion": "stopped", "last_motion_at": None},
    )
    patio.arm.side_effect = CameraError("host unreachable")
    bot = CatTvBot(
        TelegramConfig(bot_token="token", allowed_user_ids={10}),
        {"Kitchen": kitchen, "Patio": patio},
        "Kitchen",
    )
    update = _command_update()

    await bot.arm(update, Mock())

    update.effective_message.reply_text.assert_awaited_once_with(
        "Armed: Kitchen\nUnavailable: Patio"
    )


def _camera(name: str, status: dict[str, object] | Exception) -> Mock:
    camera = Mock()
    camera.name = name
    camera.status = AsyncMock(
        side_effect=status if isinstance(status, Exception) else None,
        return_value=None if isinstance(status, Exception) else status,
    )
    camera.arm = AsyncMock()
    camera.disarm = AsyncMock()
    camera.capture_photo = AsyncMock()
    camera.record_clip = AsyncMock()
    return camera


def _command_update() -> Mock:
    update = Mock()
    update.effective_user.id = 10
    update.effective_chat.id = 20
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def _callback_update(data: str) -> Mock:
    update = Mock()
    update.effective_user.id = 10
    update.effective_chat.id = 20
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update
