import asyncio
from datetime import datetime
from pathlib import Path
import subprocess
import sys

from aiohttp import ClientSession
from pydantic import ValidationError
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
import typer

from gattv.bot import CatTvBot
from gattv.camera import CameraError, CameraService
from gattv.config import Config, DEFAULT_CONFIG_PATH, load_config
from gattv.motion import MotionSample, motion_samples
from gattv.node_server import (
    HubMotionReporter,
    create_hub_app,
    create_node_app,
    start_web_app,
)
from gattv.nodes import LocalCameraNode, RemoteCameraNode


app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Cat monitoring service."""


@app.command()
def server(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run the Telegram hub and its local/remote cameras."""
    config = _load_cli_config(config_path)
    if config.telegram is None or config.hub is None or not config.cameras:
        console.print(
            "[bold red]Hub config requires [telegram], [hub], and [[cameras]].[/]"
        )
        raise typer.Exit(1)
    if config.hub.default_camera not in {camera.name for camera in config.cameras}:
        console.print("[bold red]hub.default_camera must name a configured camera.[/]")
        raise typer.Exit(1)

    caffeinate = _start_caffeinate()
    try:
        asyncio.run(_run_hub(config_path, config))
    except KeyboardInterrupt:
        pass
    finally:
        _stop_caffeinate(caffeinate)
        console.print("[yellow]gattv server stopped.[/]")


@app.command()
def node(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run a camera node for a hub on the local network."""
    config = _load_cli_config(config_path)
    if config.node is None:
        console.print("[bold red]Node config requires a [node] section.[/]")
        raise typer.Exit(1)

    caffeinate = _start_caffeinate()
    try:
        asyncio.run(_run_node(config))
    except KeyboardInterrupt:
        pass
    finally:
        _stop_caffeinate(caffeinate)
        console.print("[yellow]gattv node stopped.[/]")


async def _run_hub(config_path: Path, config: Config) -> None:
    assert config.telegram is not None
    assert config.hub is not None
    async with ClientSession() as session:
        bot: CatTvBot | None = None

        async def local_motion(name: str, path: Path | None = None) -> None:
            if bot is not None:
                await bot.notify_motion(name, path)

        cameras = {}
        for target in config.cameras:
            if target.url is None:
                cameras[target.name] = LocalCameraNode(
                    target.name,
                    CameraService(config.camera),
                    config.motion,
                    lambda text, name=target.name: local_motion(name),
                    lambda path, name=target.name: local_motion(name, path),
                )
            else:
                cameras[target.name] = RemoteCameraNode(
                    target.name,
                    target.url,
                    config.hub.shared_token,
                    session,
                    config.camera.clip_seconds,
                )

        bot = CatTvBot(config.telegram, cameras, config.hub.default_camera)
        application = bot.build_application()
        runner = await start_web_app(
            create_hub_app(config.hub.shared_token, bot.notify_motion),
            config.hub.listen_host,
            config.hub.listen_port,
        )
        status = _ServerStatus(config_path, config, bot)
        try:
            await application.initialize()
            await application.start()
            if application.updater is None:
                raise RuntimeError("Telegram updater is unavailable.")
            await application.updater.start_polling()
            with Live(status, console=console, refresh_per_second=1):
                await asyncio.Event().wait()
        finally:
            if application.updater is not None and application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
            await application.shutdown()
            await bot.close()
            await runner.cleanup()


async def _run_node(config: Config) -> None:
    assert config.node is not None
    async with ClientSession() as session:
        reporter = HubMotionReporter(
            config.node.name,
            config.node.hub_url,
            config.node.shared_token,
            session,
        )
        camera_node = LocalCameraNode(
            config.node.name,
            CameraService(config.camera),
            config.motion,
            reporter.notify,
            reporter.send_video,
        )
        runner = await start_web_app(
            create_node_app(camera_node, config.node.shared_token),
            config.node.listen_host,
            config.node.listen_port,
        )
        console.print(
            f"[bold green]Camera node {config.node.name} listening on "
            f"{config.node.listen_host}:{config.node.listen_port}[/]"
        )
        try:
            await asyncio.Event().wait()
        finally:
            await camera_node.close()
            await runner.cleanup()


@app.command("motion-test")
def motion_test(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run motion detection locally without starting Telegram."""
    config = _load_cli_config(config_path)
    camera = CameraService(config.camera)
    console.print(
        "[dim]Running motion detection. Move in front of the camera; press Ctrl+C to stop.[/dim]"
    )
    try:
        with Live(
            _motion_table(config, None), console=console, refresh_per_second=4
        ) as live:
            for sample in motion_samples(camera, config.motion):
                live.update(_motion_table(config, sample))
    except KeyboardInterrupt:
        console.print("[yellow]Motion test stopped.[/]")
    except CameraError as error:
        console.print(f"[bold red]Camera error:[/] {error}")
        raise typer.Exit(1) from None


def _load_cli_config(config_path: Path) -> Config:
    try:
        return load_config(config_path)
    except FileNotFoundError:
        console.print(f"[bold red]Config file not found:[/] {config_path}")
        console.print("Create one with: [bold]cp gattv.example.toml gattv.toml[/]")
        raise typer.Exit(1) from None
    except ValidationError as error:
        console.print(f"[bold red]Invalid config:[/] {config_path}")
        console.print(error)
        raise typer.Exit(1) from None


def _start_caffeinate() -> subprocess.Popen[bytes] | None:
    if sys.platform != "darwin":
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i"])
    except FileNotFoundError:
        console.print(
            "[yellow]Could not find caffeinate; system sleep is not prevented.[/]"
        )
        return None


def _stop_caffeinate(caffeinate: subprocess.Popen[bytes] | None) -> None:
    if caffeinate is None or caffeinate.poll() is not None:
        return
    caffeinate.terminate()
    try:
        caffeinate.wait(timeout=2)
    except subprocess.TimeoutExpired:
        caffeinate.kill()


class _ServerStatus:
    def __init__(self, config_path: Path, config: Config, bot: CatTvBot) -> None:
        self.config_path = config_path
        self.config = config
        self.bot = bot

    def __rich__(self) -> Panel:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()
        table.add_row("Config", str(self.config_path))
        table.add_row("Cameras", ", ".join(self.bot.cameras))
        table.add_row("Default camera", self.bot.default_camera)
        table.add_row("Notify chats", self._notify_status())
        table.add_row("Current task", self.bot.state.current_task)
        table.add_row(
            "Last Telegram message", _format_timestamp(self.bot.state.last_message_at)
        )
        table.add_row("Commands", "/cameras  /status  /arm  /disarm  /photo  /video")
        table.add_row("Stop", "Ctrl+C")
        return Panel(
            table,
            title="[bold green]gattv multicamera hub[/]",
            subtitle="Polling Telegram and receiving motion events",
            border_style="green",
        )

    def _notify_status(self) -> str:
        enabled = sum(1 for enabled in self.bot.state.notify_chats.values() if enabled)
        known = len(self.bot.state.notify_chats)
        return f"{enabled} enabled / {known} known"


def _format_timestamp(value: datetime | None) -> str:
    return "-" if value is None else value.strftime("%H:%M:%S")


def _motion_table(config: Config, sample: MotionSample | None) -> Table:
    motion = config.motion
    state = "waiting" if sample is None else "motion" if sample.detected else "still"
    changed_pixels = "-" if sample is None else str(sample.changed_pixels)
    consecutive_frames = "-" if sample is None else str(sample.consecutive_frames)
    table = Table(title="gattv motion test")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("State", state)
    table.add_row("Changed pixels", f"{changed_pixels} / {motion.changed_pixels}")
    table.add_row(
        "Consecutive frames", f"{consecutive_frames} / {motion.consecutive_frames}"
    )
    table.add_row("Sensitivity", str(motion.sensitivity))
    table.add_row("Detection FPS", str(motion.detection_fps))
    table.add_row("Resize width", str(motion.resize_width))
    return table
