from dataclasses import dataclass

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from gattv.config import TelegramConfig


@dataclass
class BotState:
    armed: bool = False


class CatTvBot:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.state = BotState()

    def build_application(self) -> Application:
        application = Application.builder().token(self.config.bot_token).build()
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("arm", self.arm))
        application.add_handler(CommandHandler("disarm", self.disarm))
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        await self._reply(update, "gattv is running. Use /status, /arm, or /disarm.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        status = "armed" if self.state.armed else "disarmed"
        await self._reply(update, f"Status: {status}")

    async def arm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        self.state.armed = True
        await self._reply(update, "Armed. Motion clips will be sent once detection is connected.")

    async def disarm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return

        self.state.armed = False
        await self._reply(update, "Disarmed.")

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
