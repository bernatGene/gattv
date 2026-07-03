from dataclasses import dataclass
from pathlib import Path
import asyncio

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from gattv.camera import CameraError, CameraService
from gattv.config import TelegramConfig


@dataclass
class BotState:
    armed: bool = False


class CatTvBot:
    def __init__(self, config: TelegramConfig, camera: CameraService) -> None:
        self.config = config
        self.camera = camera
        self.state = BotState()

    def build_application(self) -> Application:
        application = Application.builder().token(self.config.bot_token).build()
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("arm", self.arm))
        application.add_handler(CommandHandler("disarm", self.disarm))
        application.add_handler(CommandHandler("photo", self.photo))
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        await self._reply(
            update, "gattv is running. Use /status, /arm, /disarm, or /photo."
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        status = "armed" if self.state.armed else "disarmed"
        await self._reply(update, f"Status: {status}")

    async def arm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        self.state.armed = True
        await self._reply(
            update, "Armed. Motion clips will be sent once detection is connected."
        )

    async def disarm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        self.state.armed = False
        await self._reply(update, "Disarmed.")

    async def photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        await self._reply(update, "Taking photo...")
        path: Path | None = None
        try:
            path = await asyncio.to_thread(self.camera.capture_photo)
            message = update.effective_message
            if message is not None:
                with path.open("rb") as photo_file:
                    await message.reply_photo(photo=photo_file)
        except CameraError as error:
            await self._reply(update, f"Camera error: {error}")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    async def _authorize(self, update: Update) -> bool:
        user = update.effective_user
        if user is not None and user.id in self.config.allowed_user_ids:
            return True

        await self._reply(update, "Not authorized.")
        return False

    async def _reply(self, update: Update, text: str) -> None:
        message = update.effective_message
        if message is not None:
            await message.reply_text(text)
