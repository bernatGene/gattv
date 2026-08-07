import asyncio
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError
from rich.console import Console
import typer

from gattv.camera_server import CameraServer
from gattv.config import DEFAULT_CONFIG_PATH, load_camera_config, load_hub_config
from gattv.config_wizard import ConfigWizard
from gattv.hub_server import HubServer
from gattv.runtime import start_caffeinate, stop_caffeinate


app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True, help="Create gattv configuration files.")
app.add_typer(config_app, name="config")
console = Console()
wizard = ConfigWizard(console)


@app.callback()
def main() -> None:
    """Cat monitoring service."""


@app.command()
def hub(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run the Telegram bot and coordinate camera processes."""
    config = _load_config(config_path, load_hub_config)
    if (
        config.hub.default_camera is not None
        and config.hub.default_camera not in config.hub.cameras
    ):
        console.print("[bold red]Invalid config:[/] default_camera is not configured")
        raise typer.Exit(1)
    asyncio.run(HubServer(config, config_path, console).run())


@app.command()
def camera(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Run one camera process."""
    config = _load_config(config_path, load_camera_config)
    server = CameraServer(config)
    caffeinate = start_caffeinate(console)
    try:
        web.run_app(
            server.build_application(),
            host=config.listen_host,
            port=config.listen_port,
            print=None,
        )
    finally:
        stop_caffeinate(caffeinate)


@config_app.command("init-hub")
def init_hub(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Interactively create a hub configuration."""
    wizard.init_hub(config_path)


@config_app.command("init")
def init_config(
    kind: str = typer.Argument(help="Configuration type: hub or camera."),
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    """Interactively create a hub or camera configuration."""
    if kind == "hub":
        init_hub(config_path)
    elif kind == "camera":
        init_camera(config_path)
    else:
        console.print("[bold red]Configuration type must be 'hub' or 'camera'.[/]")
        raise typer.Exit(1)


@config_app.command("init-camera")
def init_camera(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Interactively create and register a camera configuration."""
    wizard.init_camera(config_path)


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
