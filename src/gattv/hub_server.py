import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile

from aiohttp import web
from pydantic import ValidationError
from rich.console import Console
import typer
from zeroconf import IPVersion, Zeroconf

from gattv.bot import CatTvBot
from gattv.camera_client import CameraClient
from gattv.config import HubServerConfig
from gattv.runtime import start_caffeinate, stop_caffeinate
from gattv.setup import CameraRegistration, hub_service_info, local_ip, write_hub_config


class HubServer:
    def __init__(
        self, config: HubServerConfig, config_path: Path, console: Console
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.console = console
        self.cameras = {
            name: CameraClient(name, url) for name, url in config.hub.cameras.items()
        }
        self.bot = CatTvBot(config.telegram, self.cameras, config.hub.default_camera)

    async def run(self) -> None:
        caffeinate = start_caffeinate(self.console)
        telegram = self.bot.build_application()
        receiver = self.build_application()
        runner = web.AppRunner(receiver)
        await runner.setup()
        site = web.TCPSite(
            runner, self.config.hub.listen_host, self.config.hub.listen_port
        )
        await telegram.initialize()
        await telegram.start()
        if telegram.updater is None:
            raise RuntimeError("Telegram updater is unavailable.")
        await telegram.updater.start_polling()
        await site.start()
        address = local_ip()
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        service_info = hub_service_info(address, self.config.hub.listen_port)
        await zeroconf.async_register_service(service_info)
        self.console.print(
            f"[bold green]gattv hub running[/] with {len(self.cameras)} camera(s); "
            "Ctrl+C to stop"
        )
        self.console.print(f"Hub URL: http://{address}:{self.config.hub.listen_port}")
        try:
            await asyncio.Event().wait()
        finally:
            await zeroconf.async_unregister_service(service_info)
            zeroconf.close()
            await runner.cleanup()
            await telegram.updater.stop()
            await telegram.stop()
            await telegram.shutdown()
            stop_caffeinate(caffeinate)

    def build_application(self) -> web.Application:
        application = web.Application(client_max_size=50 * 1024 * 1024)
        application.add_routes(
            [
                web.post("/motion/{camera_name}", self.motion),
                web.post("/register", self.register),
            ]
        )
        return application

    async def register(self, request: web.Request) -> web.Response:
        try:
            registration = CameraRegistration.model_validate(await request.json())
        except (ValueError, ValidationError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        if registration.name in self.cameras:
            raise web.HTTPConflict(
                text="A camera with this name is already configured."
            )
        approved = await asyncio.to_thread(
            typer.confirm,
            f"Register camera '{registration.name}' at {registration.url}?",
            default=False,
        )
        if not approved:
            raise web.HTTPForbidden(text="Registration rejected by hub operator.")
        self.cameras[registration.name] = CameraClient(
            registration.name, registration.url
        )
        self.config.hub.cameras[registration.name] = registration.url
        if self.config.hub.default_camera is None:
            self.config.hub.default_camera = registration.name
            self.bot.default_camera = registration.name
        write_hub_config(self.config_path, self.config)
        self.console.print(f"[bold green]Registered camera:[/] {registration.name}")
        return web.json_response({"registered": True})

    async def motion(self, request: web.Request) -> web.Response:
        camera_name = request.match_info["camera_name"]
        if camera_name not in self.cameras:
            raise web.HTTPNotFound(text="Unknown camera.")
        if request.content_type.startswith("multipart/"):
            await self._receive_motion_video(request, camera_name)
        else:
            data = await request.post()
            text = str(data.get("text", "Motion detected."))
            self.console.print(f"[bold yellow]{camera_name}:[/] {text}")
            sent = await self.bot.notify_motion(camera_name, text)
            if sent == 0:
                self.console.print(
                    "[yellow]No Telegram chats have motion notifications enabled; "
                    "send /notify_on to the bot.[/]"
                )
        return web.Response()

    async def _receive_motion_video(
        self, request: web.Request, camera_name: str
    ) -> None:
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "video":
            raise web.HTTPBadRequest(text="Missing video.")
        with NamedTemporaryFile(
            prefix=f"gattv-{camera_name}-motion-", suffix=".mp4", delete=False
        ) as file:
            path = Path(file.name)
            while chunk := await part.read_chunk():
                file.write(chunk)
        try:
            self.console.print(
                f"[bold yellow]{camera_name}:[/] Motion video received; sending to Telegram..."
            )
            sent = await self.bot.send_motion_video(camera_name, path)
            if sent == 0:
                self.console.print(
                    "[yellow]Motion video not sent: no Telegram chats have motion "
                    "notifications enabled.[/]"
                )
            else:
                self.console.print(
                    f"[green]{camera_name}: Motion video sent to {sent} chat(s).[/]"
                )
        finally:
            path.unlink(missing_ok=True)
