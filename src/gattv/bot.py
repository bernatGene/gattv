from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from gattv.camera import CameraError
from gattv.camera_client import CameraClient
from gattv.config import TelegramConfig


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
        cameras: dict[str, CameraClient],
        default_camera: str | None,
    ) -> None:
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
        application.add_handler(CommandHandler("cameras", self.choose_camera))
        application.add_handler(CommandHandler("arm", self.arm))
        application.add_handler(CommandHandler("disarm", self.disarm))
        application.add_handler(CommandHandler("notify_on", self.notify_on))
        application.add_handler(CommandHandler("notify_off", self.notify_off))
        application.add_handler(CommandHandler("photo", self.photo))
        application.add_handler(CommandHandler("video", self.video))
        application.add_handler(
            CallbackQueryHandler(self.select_camera, pattern="^camera:")
        )
        application.add_error_handler(self.error_handler)
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._authorize(update):
            self._remember_chat(update)
            await self._reply(
                update,
                "gattv is running. Use /cameras to choose a camera, then /photo or /video.",
            )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        lines = []
        for name, camera in self.cameras.items():
            try:
                state = await camera.status()
                armed = "armed" if state["armed"] else "disarmed"
                lines.append(f"{name}: {armed}; motion: {state['motion']}")
            except CameraError as error:
                lines.append(f"{name}: offline ({error})")
        await self._reply(update, "\n".join(lines) or "No cameras configured.")

    async def choose_camera(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        if not self.cameras:
            await self._reply(update, "No cameras configured.")
            return
        keyboard = [
            [InlineKeyboardButton(name, callback_data=f"camera:{name}")]
            for name in self.cameras
        ]
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Choose a camera:", reply_markup=InlineKeyboardMarkup(keyboard)
            )

    async def select_camera(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        if not self.cameras:
            await self._reply(update, "No cameras configured.")
            return
        query = update.callback_query
        chat = update.effective_chat
        if query is None or query.data is None or chat is None:
            return
        name = query.data.removeprefix("camera:")
        if name not in self.cameras:
            await query.answer("Unknown camera.")
            return
        self.state.selected_cameras[chat.id] = name
        await query.answer()
        await query.edit_message_text(f"Selected camera: {name}")

    async def arm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_for_all(update, "arm")

    async def disarm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_for_all(update, "disarm")

    async def notify_on(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if await self._authorize(update):
            self._set_chat_notify(update, True)
            await self._reply(update, "Motion notifications enabled for this chat.")

    async def notify_off(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if await self._authorize(update):
            self._set_chat_notify(update, False)
            await self._reply(update, "Motion notifications disabled for this chat.")

    async def photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_capture(update, "photo")

    async def video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_capture(update, "video")

    async def notify_motion(self, camera_name: str, text: str) -> None:
        for chat_id, enabled in list(self.state.notify_chats.items()):
            if enabled and self.application is not None:
                await self.application.bot.send_message(
                    chat_id=chat_id, text=f"{camera_name}: {text}"
                )

    async def send_motion_video(self, camera_name: str, path: Path) -> None:
        for chat_id, enabled in list(self.state.notify_chats.items()):
            if enabled and self.application is not None:
                with path.open("rb") as video_file:
                    await self.application.bot.send_video(
                        chat_id=chat_id,
                        video=video_file,
                        filename=f"{camera_name}-motion.mp4",
                        caption=f"Motion: {camera_name}",
                        supports_streaming=True,
                    )

    async def _run_for_all(self, update: Update, action: str) -> None:
        if not await self._authorize(update):
            return
        failures = []
        for camera in self.cameras.values():
            try:
                await getattr(camera, action)()
            except CameraError as error:
                failures.append(str(error))
        message = f"All cameras {action}ed."
        if failures:
            message = "Some cameras failed: " + "; ".join(failures)
        await self._reply(update, message)

    async def _send_capture(self, update: Update, kind: str) -> None:
        if not await self._authorize(update):
            return
        camera = self._camera_for(update)
        if camera is None:
            await self._reply(update, "No cameras configured.")
            return
        await self._reply(update, f"Capturing {kind} from {camera.name}...")
        path: Path | None = None
        try:
            path = await (
                camera.capture_photo() if kind == "photo" else camera.record_clip()
            )
            message = update.effective_message
            if message is not None:
                with path.open("rb") as media:
                    if kind == "photo":
                        await message.reply_photo(photo=media, caption=camera.name)
                    else:
                        await message.reply_video(
                            video=media,
                            filename=f"{camera.name}.mp4",
                            caption=camera.name,
                            supports_streaming=True,
                        )
        except (CameraError, TelegramError) as error:
            await self._reply(update, f"Camera error: {error}")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def _camera_for(self, update: Update) -> CameraClient | None:
        chat = update.effective_chat
        name = self.default_camera
        if chat is not None:
            name = self.state.selected_cameras.get(chat.id, name)
        return self.cameras.get(name) if name is not None else None

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

    async def _send_text_reply(self, send: Awaitable[object]) -> None:
        try:
            await send
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            print(f"Telegram send failed: {error}")

    async def error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        print(f"Bot error: {context.error}")

    def _remember_chat(self, update: Update) -> None:
        chat = update.effective_chat
        if chat is not None:
            self.state.notify_chats.setdefault(chat.id, False)

    def _set_chat_notify(self, update: Update, enabled: bool) -> None:
        chat = update.effective_chat
        if chat is not None:
            self.state.notify_chats[chat.id] = enabled
