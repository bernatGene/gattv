from dataclasses import dataclass
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path
import asyncio

from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from gattv.camera import CameraError, CameraService
from gattv.config import MotionConfig, TelegramConfig
from gattv.motion import MotionService


@dataclass
class BotState:
    notify_chats: dict[int, bool]
    current_task: str = "idle"
    last_message_at: datetime | None = None


class CatTvBot:
    def __init__(
        self, config: TelegramConfig, camera: CameraService, motion_config: MotionConfig
    ) -> None:
        self.config = config
        self.camera = camera
        self.state = BotState(notify_chats={})
        self.camera_lock = asyncio.Lock()
        self.motion = MotionService(
            camera,
            motion_config,
            self.camera_lock,
            self._notify_motion,
            self._send_motion_video,
        )
        self.application: Application | None = None

    def build_application(self) -> Application:
        application = Application.builder().token(self.config.bot_token).build()
        self.application = application
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("arm", self.arm))
        application.add_handler(CommandHandler("disarm", self.disarm))
        application.add_handler(CommandHandler("notify_on", self.notify_on))
        application.add_handler(CommandHandler("notify_off", self.notify_off))
        application.add_handler(CommandHandler("photo", self.photo))
        application.add_handler(CommandHandler("video", self.video))
        application.add_error_handler(self.error_handler)
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)

        await self._reply(
            update,
            "gattv is running. Use /status, /arm, /disarm, /notify_on, /notify_off, /photo, or /video.",
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)

        armed = "armed" if self.motion.state.armed else "disarmed"
        notify = "on" if self._chat_notify_enabled(update) else "off"
        await self._reply(
            update,
            f"Status: {armed}; motion: {self.motion.state.status}; notifications: {notify}",
        )

    async def arm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)

        started = await self.motion.arm()
        message = (
            "Armed. Use /notify_on in this chat to receive motion notifications."
            if started
            else "Already armed."
        )
        await self._reply(update, message)

    async def disarm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)

        stopped = await self.motion.disarm()
        message = "Disarmed." if stopped else "Already disarmed."
        await self._reply(update, message)

    async def notify_on(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        self._set_chat_notify(update, True)
        await self._reply(
            update,
            "Motion notifications enabled for this chat. Use /arm to start detection if needed.",
        )

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

        was_paused = await self.motion.pause()
        if self.camera_lock.locked():
            await self._reply(update, "Camera busy, try again in a moment.")
            if was_paused:
                self.motion.resume()
            return

        await self._reply(update, "Taking photo...")
        path: Path | None = None
        await self.camera_lock.acquire()
        try:
            self.state.current_task = "capturing photo"
            path = await asyncio.to_thread(self.camera.capture_photo)
            message = update.effective_message
            if message is not None:
                with path.open("rb") as photo_file:
                    self.state.current_task = "sending photo"
                    await self._send_photo_reply(message, photo_file)
        except CameraError as error:
            await self._reply(update, f"Camera error: {error}")
        finally:
            self.state.current_task = "idle"
            self.camera_lock.release()
            if path is not None:
                path.unlink(missing_ok=True)
            if was_paused:
                self.motion.resume()

    async def video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)

        was_paused = await self.motion.pause()
        if self.camera_lock.locked():
            await self._reply(update, "Camera busy, try again in a moment.")
            if was_paused:
                self.motion.resume()
            return

        await self._reply(
            update, f"Recording {self.camera.config.clip_seconds}s video..."
        )
        path: Path | None = None
        await self.camera_lock.acquire()
        try:
            self.state.current_task = "recording/encoding video"
            path = await asyncio.to_thread(self.camera.record_clip)
            message = update.effective_message
            if message is not None:
                with path.open("rb") as video_file:
                    self.state.current_task = "sending video"
                    await self._send_video_reply(message, video_file)
        except CameraError as error:
            await self._reply(update, f"Camera error: {error}")
        finally:
            self.state.current_task = "idle"
            self.camera_lock.release()
            if path is not None:
                path.unlink(missing_ok=True)
            if was_paused:
                self.motion.resume()

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

    async def _notify_motion(self, text: str) -> None:
        if self.application is None:
            return

        try:
            for chat_id, enabled in list(self.state.notify_chats.items()):
                if enabled:
                    self.state.current_task = "sending motion notification"
                    await self._send_bot_message(chat_id, text)
        finally:
            self.state.current_task = "idle"

    async def _send_motion_video(self, path: Path) -> None:
        if self.application is None:
            return

        try:
            for chat_id, enabled in list(self.state.notify_chats.items()):
                if enabled:
                    self.state.current_task = "sending motion video"
                    with path.open("rb") as video_file:
                        await self._send_bot_video(chat_id, video_file)
        finally:
            self.state.current_task = "idle"

    async def _send_text_reply(self, send: Awaitable[object]) -> None:
        self.state.current_task = "sending message"
        try:
            await send
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            self._log_telegram_error(error)
        finally:
            self.state.current_task = "idle"

    async def _send_photo_reply(self, message: Message, photo_file: object) -> None:
        try:
            await message.reply_photo(photo=photo_file)
            self.state.last_message_at = datetime.now()
        except TelegramError as error:
            self._log_telegram_error(error)

    async def _send_video_reply(self, message: Message, video_file: object) -> None:
        try:
            await message.reply_video(
                video=video_file,
                filename="gattv-video.mp4",
                caption="Video clip",
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

    async def _send_bot_video(self, chat_id: int, video_file: object) -> None:
        if self.application is None:
            return

        try:
            await self.application.bot.send_video(
                chat_id=chat_id,
                video=video_file,
                filename="gattv-motion.mp4",
                caption="Motion clip",
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
