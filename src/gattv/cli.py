from pathlib import Path

from pydantic import ValidationError
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gattv.bot import CatTvBot
from gattv.camera import CameraService
from gattv.config import DEFAULT_CONFIG_PATH, load_config


app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Cat monitoring service."""


@app.command()
def server(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run the Telegram bot server."""
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        console.print(f"[bold red]Config file not found:[/] {config_path}")
        console.print("Create one with: [bold]cp gattv.example.toml gattv.toml[/]")
        raise typer.Exit(1) from None
    except ValidationError as error:
        console.print(f"[bold red]Invalid config:[/] {config_path}")
        console.print(error)
        raise typer.Exit(1) from None

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
    table.add_row("Commands", "/start  /status  /arm  /disarm  /photo")
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

    try:
        bot.build_application().run_polling()
    finally:
        console.print("[yellow]gattv server stopped.[/]")
