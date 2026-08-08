import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

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


@dataclass(frozen=True)
class CameraSnapshot:
    name: str
    armed: bool | None
    motion: str | None
    last_motion_at: str | None = None


CameraAction = Literal["arm", "disarm"]


class CatTvBot:
    def __init__(
        self,
        config: TelegramConfig,
        cameras: dict[str, CameraClient],
        default_camera: str | None,
        save_config: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.cameras = cameras
        self.default_camera = default_camera
        self.save_config = save_config
        self.state = BotState(
            notify_chats={chat_id: True for chat_id in config.notify_chat_ids},
            selected_cameras={},
        )
        self.application: Application | None = None
        self._camera_callback_ids: dict[str, str] = {}
        self._camera_names_by_callback_id: dict[str, str] = {}

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
            CallbackQueryHandler(self.camera_callback, pattern="^gattv:")
        )
        application.add_error_handler(self.error_handler)
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        await self._show_dashboard(update, "gattv is running.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        await self._show_dashboard(update)

    async def choose_camera(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        self._remember_chat(update)
        await self._show_dashboard(update)

    async def camera_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorize(update):
            return
        query = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()
        self._remember_chat(update)
        data = query.data.removeprefix("gattv:")
        if data == "dashboard":
            await self._show_dashboard(update)
            return
        if data == "notify":
            chat = update.effective_chat
            if chat is not None:
                enabled = not self.state.notify_chats.get(chat.id, False)
                self._set_chat_notify(update, enabled)
                notice = f"Notifications {'enabled' if enabled else 'disabled'}."
                await self._show_dashboard(update, notice)
            return
        if data in {"arm-all", "disarm-all"}:
            action: CameraAction = "arm" if data == "arm-all" else "disarm"
            successes, failures = await self._change_all(action)
            await self._show_dashboard(
                update, self._bulk_result(action, successes, failures)
            )
            return

        parts = data.split(":", 1)
        if len(parts) != 2:
            await self._show_dashboard(update, "Unknown action.")
            return
        action, callback_id = parts
        name = self._camera_names_by_callback_id.get(callback_id)
        camera = self.cameras.get(name) if name is not None else None
        if camera is None:
            await self._show_dashboard(update, "That camera is no longer available.")
            return
        if action == "view":
            await self._show_camera(update, camera)
            return
        if action in {"arm", "disarm"}:
            notice = None
            try:
                await self._change_camera(camera, action)
            except CameraError:
                notice = f"Could not {action} {name}: camera unavailable."
            await self._show_camera(update, camera, notice)
            return
        if action == "select":
            chat = update.effective_chat
            if chat is not None:
                self.state.selected_cameras[chat.id] = name
            await self._show_camera(update, camera, f"Capture camera set to {name}.")
            return
        if action in {"photo", "video"}:
            await self._send_capture(update, action, camera)
            return
        await self._show_camera(update, camera, "Unknown action.")

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

    async def notify_motion(self, camera_name: str, text: str) -> int:
        sent = 0
        for chat_id, enabled in list(self.state.notify_chats.items()):
            if enabled and self.application is not None:
                await self.application.bot.send_message(
                    chat_id=chat_id, text=f"{camera_name}: {text}"
                )
                sent += 1
        return sent

    async def send_motion_video(self, camera_name: str, path: Path) -> int:
        sent = 0
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
                sent += 1
        return sent

    async def _run_for_all(self, update: Update, action: str) -> None:
        if not await self._authorize(update):
            return
        camera_action: CameraAction = "arm" if action == "arm" else "disarm"
        successes, failures = await self._change_all(camera_action)
        await self._reply(update, self._bulk_result(camera_action, successes, failures))

    async def _send_capture(
        self, update: Update, kind: str, camera: CameraClient | None = None
    ) -> None:
        if not await self._authorize(update):
            return
        camera = camera or self._camera_for(update)
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
        query = update.callback_query
        if query is not None:
            await query.answer("Not authorized.", show_alert=True)
        else:
            await self._reply(update, "Not authorized.")
        return False

    async def _show_dashboard(self, update: Update, notice: str | None = None) -> None:
        snapshots = await asyncio.gather(
            *(
                self._camera_snapshot(name, camera)
                for name, camera in self.cameras.items()
            )
        )
        armed = sum(snapshot.armed is True for snapshot in snapshots)
        disarmed = sum(snapshot.armed is False for snapshot in snapshots)
        unavailable = sum(snapshot.armed is None for snapshot in snapshots)
        lines = []
        if notice:
            lines.extend([notice, ""])
        if not snapshots:
            lines.append("No cameras configured.")
        else:
            lines.append(
                f"Cameras: {armed} armed, {disarmed} disarmed, "
                f"{unavailable} unavailable"
            )
            lines.append("")
            for snapshot in snapshots:
                lines.append(self._snapshot_line(snapshot))
        selected = self._camera_for(update)
        chat = update.effective_chat
        notifications = (
            self.state.notify_chats.get(chat.id, False) if chat is not None else False
        )
        if snapshots:
            lines.extend(
                [
                    "",
                    f"Capture camera: {selected.name if selected else 'none'}",
                    f"Notifications: {'On' if notifications else 'Off'}",
                ]
            )

        keyboard = []
        for snapshot in snapshots:
            label = self._snapshot_label(snapshot)
            callback_id = self._callback_id_for(snapshot.name)
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{snapshot.name}: {label}",
                        callback_data=f"gattv:view:{callback_id}",
                    )
                ]
            )
        if snapshots:
            keyboard.append(
                [
                    InlineKeyboardButton("Arm all", callback_data="gattv:arm-all"),
                    InlineKeyboardButton(
                        "Disarm all", callback_data="gattv:disarm-all"
                    ),
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"Notifications: {'On' if notifications else 'Off'}",
                        callback_data="gattv:notify",
                    ),
                    InlineKeyboardButton("Refresh", callback_data="gattv:dashboard"),
                ]
            )
        await self._send_panel(update, "\n".join(lines), keyboard)

    async def _show_camera(
        self,
        update: Update,
        camera: CameraClient,
        notice: str | None = None,
    ) -> None:
        snapshot = await self._camera_snapshot(camera.name, camera)
        lines = []
        if notice:
            lines.extend([notice, ""])
        lines.extend([camera.name, f"State: {self._snapshot_label(snapshot)}"])
        if snapshot.motion is not None:
            lines.append(f"Motion: {snapshot.motion}")
        if snapshot.last_motion_at is not None:
            lines.append(f"Last motion: {snapshot.last_motion_at}")
        selected = self._camera_for(update)
        lines.append(f"Capture camera: {'Yes' if selected is camera else 'No'}")
        callback_id = self._callback_id_for(camera.name)
        keyboard = []
        if snapshot.armed is not None:
            action = "disarm" if snapshot.armed else "arm"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{action.title()} {camera.name}",
                        callback_data=f"gattv:{action}:{callback_id}",
                    )
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "Photo", callback_data=f"gattv:photo:{callback_id}"
                    ),
                    InlineKeyboardButton(
                        "Video", callback_data=f"gattv:video:{callback_id}"
                    ),
                ]
            )
            if selected is not camera:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            "Use for capture",
                            callback_data=f"gattv:select:{callback_id}",
                        )
                    ]
                )
        else:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "Retry", callback_data=f"gattv:view:{callback_id}"
                    )
                ]
            )
        keyboard.append([InlineKeyboardButton("Back", callback_data="gattv:dashboard")])
        await self._send_panel(update, "\n".join(lines), keyboard)

    async def _send_panel(
        self, update: Update, text: str, keyboard: list[list[InlineKeyboardButton]]
    ) -> None:
        markup = InlineKeyboardMarkup(keyboard)
        query = update.callback_query
        if query is not None:
            await self._send_text_reply(
                query.edit_message_text(text=text, reply_markup=markup)
            )
            return
        message = update.effective_message
        if message is not None:
            await self._send_text_reply(
                message.reply_text(text=text, reply_markup=markup)
            )

    async def _camera_snapshot(self, name: str, camera: CameraClient) -> CameraSnapshot:
        try:
            state = await camera.status()
            if not isinstance(state, dict):
                raise ValueError("Invalid camera status")
            armed = state.get("armed")
            motion = state.get("motion")
            last_motion_at = state.get("last_motion_at")
            if type(armed) is not bool or not isinstance(motion, str):
                raise ValueError("Invalid camera status")
            if last_motion_at is not None and not isinstance(last_motion_at, str):
                raise ValueError("Invalid last motion time")
            return CameraSnapshot(name, armed, motion, last_motion_at)
        except (CameraError, TypeError, ValueError):
            return CameraSnapshot(name, None, None)

    async def _change_all(self, action: CameraAction) -> tuple[list[str], list[str]]:
        successes = []
        failures = []
        for name, camera in self.cameras.items():
            try:
                await self._change_camera(camera, action)
                successes.append(name)
            except CameraError:
                failures.append(name)
        return successes, failures

    async def _change_camera(self, camera: CameraClient, action: CameraAction) -> None:
        if action == "arm":
            await camera.arm()
        else:
            await camera.disarm()

    def _bulk_result(
        self, action: CameraAction, successes: list[str], failures: list[str]
    ) -> str:
        if not successes and not failures:
            return "No cameras configured."
        verb = "Armed" if action == "arm" else "Disarmed"
        if not failures:
            return f"All cameras {verb.lower()}."
        lines = []
        if successes:
            lines.append(f"{verb}: {', '.join(successes)}")
        lines.append(f"Unavailable: {', '.join(failures)}")
        return "\n".join(lines)

    def _callback_id_for(self, name: str) -> str:
        callback_id = self._camera_callback_ids.get(name)
        if callback_id is not None:
            return callback_id
        callback_id = str(len(self._camera_callback_ids) + 1)
        self._camera_callback_ids[name] = callback_id
        self._camera_names_by_callback_id[callback_id] = name
        return callback_id

    def _snapshot_line(self, snapshot: CameraSnapshot) -> str:
        if snapshot.armed is None:
            return f"{snapshot.name}: Unavailable"
        return (
            f"{snapshot.name}: {self._snapshot_label(snapshot)}; "
            f"motion: {snapshot.motion}"
        )

    def _snapshot_label(self, snapshot: CameraSnapshot) -> str:
        if snapshot.armed is None:
            return "Unavailable"
        return "Armed" if snapshot.armed else "Disarmed"

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
            if enabled:
                self.config.notify_chat_ids.add(chat.id)
            else:
                self.config.notify_chat_ids.discard(chat.id)
            if self.save_config is not None:
                self.save_config()
