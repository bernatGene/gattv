from pathlib import Path
import subprocess
import sys

from pydantic import ValidationError
import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from gattv.bot import CatTvBot
from gattv.camera import CameraError, CameraService
from gattv.config import Config, DEFAULT_CONFIG_PATH, load_config
from gattv.motion import MotionSample, motion_samples


app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Cat monitoring service."""


@app.command()
def server(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run the Telegram bot server."""
    config = _load_cli_config(config_path)

    camera = CameraService(config.camera)
    bot = CatTvBot(config.telegram, camera)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Config", str(config_path))
    table.add_row("Allowed users", str(len(config.telegram.allowed_user_ids)))
    table.add_row(
        "Camera",
        f"index {config.camera.index}, {config.camera.width}x{config.camera.height} @ {config.camera.fps} fps",
    )
    table.add_row("Warmup frames", str(config.camera.warmup_frames))
    table.add_row("Clip length", f"{config.camera.clip_seconds}s")
    sleep_status = (
        "prevented while server runs" if sys.platform == "darwin" else "system default"
    )
    table.add_row("Sleep", sleep_status)
    table.add_row("Commands", "/start  /status  /arm  /disarm  /photo  /video")
    table.add_row("Stop", "Ctrl+C")

    console.print(
        Panel(
            table,
            title="[bold green]gattv Telegram bot[/]",
            subtitle="Polling Telegram for messages",
            border_style="green",
        )
    )
    console.print(
        "[dim]Waiting for messages. Open Telegram and send /start to the bot.[/dim]"
    )

    caffeinate = _start_caffeinate()
    try:
        bot.build_application().run_polling()
    finally:
        _stop_caffeinate(caffeinate)
        console.print("[yellow]gattv server stopped.[/]")


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
