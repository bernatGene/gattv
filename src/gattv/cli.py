import asyncio
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile

from aiohttp import web
from pydantic import ValidationError
from rich.console import Console
import typer

from gattv.bot import CatTvBot
from gattv.camera_client import CameraClient
from gattv.camera_server import CameraServer
from gattv.config import (
    DEFAULT_CONFIG_PATH,
    HubServerConfig,
    load_camera_config,
    load_hub_config,
)


app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Cat monitoring service."""


@app.command()
def hub(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run the Telegram bot and coordinate camera processes."""
    config = _load_config(config_path, load_hub_config)
    if config.hub.default_camera not in config.hub.cameras:
        console.print("[bold red]Invalid config:[/] default_camera is not configured")
        raise typer.Exit(1)
    asyncio.run(_run_hub(config))


@app.command()
def camera(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run one camera process."""
    config = _load_config(config_path, load_camera_config)
    server = CameraServer(config)
    caffeinate = _start_caffeinate()
    try:
        web.run_app(
            server.build_application(),
            host=config.listen_host,
            port=config.listen_port,
            print=None,
        )
    finally:
        _stop_caffeinate(caffeinate)


async def _run_hub(config: HubServerConfig) -> None:
    cameras = {
        name: CameraClient(name, url) for name, url in config.hub.cameras.items()
    }
    bot = CatTvBot(config.telegram, cameras, config.hub.default_camera)
    telegram = bot.build_application()

    receiver = web.Application(client_max_size=50 * 1024 * 1024)

    async def motion(request: web.Request) -> web.Response:
        camera_name = request.match_info["camera_name"]
        if camera_name not in cameras:
            raise web.HTTPNotFound(text="Unknown camera.")
        if request.content_type.startswith("multipart/"):
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
                await bot.send_motion_video(camera_name, path)
            finally:
                path.unlink(missing_ok=True)
        else:
            data = await request.post()
            await bot.notify_motion(
                camera_name, str(data.get("text", "Motion detected."))
            )
        return web.Response()

    receiver.add_routes([web.post("/motion/{camera_name}", motion)])
    runner = web.AppRunner(receiver)
    await runner.setup()
    site = web.TCPSite(runner, config.hub.listen_host, config.hub.listen_port)

    caffeinate = _start_caffeinate()
    await telegram.initialize()
    await telegram.start()
    if telegram.updater is None:
        raise RuntimeError("Telegram updater is unavailable.")
    await telegram.updater.start_polling()
    await site.start()
    console.print(
        f"[bold green]gattv hub running[/] with {len(cameras)} camera(s); Ctrl+C to stop"
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await telegram.updater.stop()
        await telegram.stop()
        await telegram.shutdown()
        _stop_caffeinate(caffeinate)


def _load_config(path: Path, loader):
    try:
        return loader(path)
    except FileNotFoundError:
        console.print(f"[bold red]Config file not found:[/] {path}")
        raise typer.Exit(1) from None
    except ValidationError as error:
        console.print(f"[bold red]Invalid config:[/] {path}")
        console.print(error)
        raise typer.Exit(1) from None


def _start_caffeinate() -> subprocess.Popen[bytes] | None:
    if sys.platform != "darwin":
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i"])
    except FileNotFoundError:
        console.print("[yellow]Could not find caffeinate; sleep is not prevented.[/]")
        return None


def _stop_caffeinate(caffeinate: subprocess.Popen[bytes] | None) -> None:
    if caffeinate is None or caffeinate.poll() is not None:
        return
    caffeinate.terminate()
    try:
        caffeinate.wait(timeout=2)
    except subprocess.TimeoutExpired:
        caffeinate.kill()
