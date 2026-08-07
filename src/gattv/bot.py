import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from gattv.camera import CameraError
from gattv.config import TelegramConfig
from gattv.nodes import CameraNode, CameraNodeStatus


@dataclass
class BotState:
    notify_chats: dict[int, bool]
    selected_cameras: dict[int, str]
    current_task: str = "idle"
    last_message_at: datetime | None = None


class CatTvBot:
    def __init__(
        self,
        config: TelegramConfig,
        cameras: dict[str, CameraNode],
        default_camera: str,
    ) -> None:
        if default_camera not in cameras:
            raise ValueError(f"Default camera {default_camera!r} is not configured.")
        self.config = config
        self.cameras = cameras
        self.default_camera = default_camera
        self.state = BotState(notify_chats={}, selected_cameras={})
        self.application: Application | None = None

    def build_application(self) -> Application:
        application = Application.builder().token(self.config.bot_token).build()
        self.application = application
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("cameras", self.camera_menu))
        application.add_handler(CommandHandler("arm", self.arm))
        application.add_handler(CommandHandler("disarm", self.disarm))
        application.add_handler(CommandHandler("notify_on", self.notify_on))
        application.add_handler(CommandHandler("notify_off", self.notify_off))
        application.add_handler(CommandHandler("photo", self.photo))
        application.add_handler(CommandHandler("video", self.video))
        application.add_handler(CallbackQueryHandler(self.camera_callback))
        application.add_error_handler(self.error_handler)
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        await self._reply(
            update,
            "gattv is running. Use /cameras to select and control a camera, or use /photo and /video for the selected camera.",
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        selected = self._selected_camera_name(update)
        lines = [f"Selected camera: {selected}"]
        statuses = await asyncio.gather(
            *(camera.status() for camera in self.cameras.values()),
            return_exceptions=True,
        )
        for name, status in zip(self.cameras, statuses):
            if isinstance(status, BaseException):
                lines.append(f"{name}: offline ({status})")
            else:
                armed = "armed" if status.armed else "disarmed"
                lines.append(f"{name}: {armed}; motion: {status.motion_status}")
        notify = "on" if self._chat_notify_enabled(update) else "off"
        lines.append(f"Notifications: {notify}")
        await self._reply(update, "\n".join(lines))

    async def camera_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        await self._reply_with_markup(
            update, "Choose a camera:", self._camera_keyboard(update)
        )

    async def camera_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        query = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()
        action, _, value = query.data.partition(":")
        chat = update.effective_chat
        if action == "select" and chat is not None and value in self.cameras:
            self.state.selected_cameras[chat.id] = value
            await query.edit_message_text(
                f"Selected camera: {value}", reply_markup=self._camera_keyboard(update)
            )
            return
        if action == "photo":
            await self.photo(update, context)
        elif action == "video":
            await self.video(update, context)
        elif action == "arm":
            await self.arm(update, context)
        elif action == "disarm":
            await self.disarm(update, context)

    async def arm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        await self._change_all_nodes(update, "arm")

    async def disarm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        await self._change_all_nodes(update, "disarm")

    async def notify_on(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        self._set_chat_notify(update, True)
        await self._reply(update, "Motion notifications enabled for this chat.")

    async def notify_off(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        self._set_chat_notify(update, False)
        await self._reply(update, "Motion notifications disabled for this chat.")

    async def photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        name, camera = self._selected_camera(update)
        await self._reply(update, f"Taking photo from {name}...")
        await self._capture_and_send(update, name, camera.capture_photo, "photo")

    async def video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        name, camera = self._selected_camera(update)
        await self._reply(
            update, f"Recording {camera.clip_seconds}s video from {name}..."
        )
        await self._capture_and_send(update, name, camera.record_clip, "video")

    async def notify_motion(self, camera_name: str, path: Path | None = None) -> None:
        if self.application is None:
            return
        try:
            for chat_id, enabled in list(self.state.notify_chats.items()):
                if not enabled:
                    continue
                self.state.current_task = f"sending {camera_name} motion"
                if path is None:
                    await self._send_bot_message(
                        chat_id, f"Motion detected: {camera_name}"
                    )
                else:
                    with path.open("rb") as video_file:
                        await self._send_bot_video(chat_id, video_file, camera_name)
        finally:
            self.state.current_task = "idle"

    async def camera_statuses(self) -> dict[str, CameraNodeStatus | None]:
        results = await asyncio.gather(
            *(camera.status() for camera in self.cameras.values()),
            return_exceptions=True,
        )
        return {
            name: None if isinstance(status, BaseException) else status
            for name, status in zip(self.cameras, results)
        }

    async def close(self) -> None:
        await asyncio.gather(*(camera.close() for camera in self.cameras.values()))

    async def _change_all_nodes(self, update: Update, action: str) -> None:
        calls = [getattr(camera, action)() for camera in self.cameras.values()]
        results = await asyncio.gather(*calls, return_exceptions=True)
        changed = sum(result is True for result in results)
        failures = [
            f"{name}: {result}"
            for name, result in zip(self.cameras, results)
            if isinstance(result, BaseException)
        ]
        message = f"{action.title()}ed {changed} camera(s)."
        if failures:
            message += "\nFailed: " + "; ".join(failures)
        await self._reply(update, message)

    async def _capture_and_send(
        self,
        update: Update,
        name: str,
        capture,
        media_type: str,
    ) -> None:
        path: Path | None = None
        try:
            self.state.current_task = f"capturing {media_type} from {name}"
            path = await capture()
            message = update.effective_message
            if message is None:
                return
            with path.open("rb") as media_file:
                self.state.current_task = f"sending {media_type} from {name}"
                if media_type == "photo":
                    await self._send_photo_reply(message, media_file, name)
                else:
                    await self._send_video_reply(message, media_file, name)
        except CameraError as error:
            await self._reply(update, f"Camera error ({name}): {error}")
        finally:
            self.state.current_task = "idle"
            if path is not None:
                path.unlink(missing_ok=True)

    def _camera_keyboard(self, update: Update) -> InlineKeyboardMarkup:
        selected = self._selected_camera_name(update)
        camera_buttons = [
            InlineKeyboardButton(
                ("✓ " if name == selected else "") + name,
                callback_data=f"select:{name}",
            )
            for name in self.cameras
        ]
        rows = [
            camera_buttons[index : index + 2]
            for index in range(0, len(camera_buttons), 2)
        ]
        rows.extend(
            [
                [
                    InlineKeyboardButton("Take photo", callback_data="photo:"),
                    InlineKeyboardButton("Record video", callback_data="video:"),
                ],
                [
                    InlineKeyboardButton("Arm all", callback_data="arm:"),
                    InlineKeyboardButton("Disarm all", callback_data="disarm:"),
                ],
            ]
        )
        return InlineKeyboardMarkup(rows)

    def _selected_camera(self, update: Update) -> tuple[str, CameraNode]:
        name = self._selected_camera_name(update)
        return name, self.cameras[name]

    def _selected_camera_name(self, update: Update) -> str:
        chat = update.effective_chat
        if chat is None:
            return self.default_camera
        return self.state.selected_cameras.get(chat.id, self.default_camera)

    async def _authorize(self, update: Update) -> bool:
        user = update.effective_user
        if user is not None and user.id in self.config.allowed_user_ids:
            return True
        await self._reply(update, "Not authorized.")
        return False

    async def _reply(self, update: Update, text: str) -> None:
        message = update.effective_message
        if message is not None:
            await self._send_text_reply(message.reply_text(text))

    async def _reply_with_markup(
        self, update: Update, text: str, markup: InlineKeyboardMarkup
    ) -> None:
        message = update.effective_message
        if message is not None:
            await self._send_text_reply(message.reply_text(text, reply_markup=markup))

    async def _send_text_reply(self, send: Awaitable[object]) -> None:
        self.state.current_task = "sending message"
        try:
            await send
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            self._log_telegram_error(error)
        finally:
            self.state.current_task = "idle"

    async def _send_photo_reply(
        self, message: Message, file: object, name: str
    ) -> None:
        try:
            await message.reply_photo(photo=file, caption=name)
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            self._log_telegram_error(error)

    async def _send_video_reply(
        self, message: Message, file: object, name: str
    ) -> None:
        try:
            await message.reply_video(
                video=file,
                filename="gattv-video.mp4",
                caption=name,
                supports_streaming=True,
            )
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            self._log_telegram_error(error)

    async def _send_bot_message(self, chat_id: int, text: str) -> None:
        if self.application is None:
            return
        try:
            await self.application.bot.send_message(chat_id=chat_id, text=text)
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            self._log_telegram_error(error)

    async def _send_bot_video(self, chat_id: int, file: object, name: str) -> None:
        if self.application is None:
            return
        try:
            await self.application.bot.send_video(
                chat_id=chat_id,
                video=file,
                filename="gattv-motion.mp4",
                caption=f"Motion detected: {name}",
                supports_streaming=True,
            )
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            self._log_telegram_error(error)

    async def error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if isinstance(context.error, TelegramError):
            self._log_telegram_error(context.error)
            return
        print(f"Unexpected bot error: {context.error}")

    def _log_telegram_error(self, error: TelegramError) -> None:
        print(f"Telegram send failed: {error}")

    def _remember_chat(self, update: Update) -> None:
        chat = update.effective_chat
        if chat is not None:
            self.state.notify_chats.setdefault(chat.id, False)

    def _set_chat_notify(self, update: Update, enabled: bool) -> None:
        chat = update.effective_chat
        if chat is not None:
            self.state.notify_chats[chat.id] = enabled

    def _chat_notify_enabled(self, update: Update) -> bool:
        chat = update.effective_chat
        return chat is not None and self.state.notify_chats.get(chat.id, False)
