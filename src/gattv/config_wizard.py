import asyncio
from pathlib import Path
import socket

import aiohttp
from rich.console import Console
import typer

from gattv.config import CameraServerConfig, HubConfig, HubServerConfig, TelegramConfig
from gattv.setup import (
    CameraRegistration,
    discover_hubs,
    local_ip,
    register_camera,
    write_camera_config,
    write_hub_config,
)


class ConfigWizard:
    def __init__(self, console: Console) -> None:
        self.console = console

    def init_hub(self, config_path: Path) -> None:
        self._confirm_overwrite(config_path)
        address = local_ip()
        self.console.print(f"Detected hub LAN address: [bold]{address}[/]")
        bot_token = typer.prompt("Telegram bot token")
        raw_user_ids = typer.prompt("Allowed Telegram user IDs (comma-separated)")
        try:
            user_ids = {int(value.strip()) for value in raw_user_ids.split(",")}
        except ValueError:
            self.console.print("[bold red]User IDs must be integers.[/]")
            raise typer.Exit(1) from None
        port = typer.prompt("Hub port", default=8765, type=int)
        config = HubServerConfig(
            telegram=TelegramConfig(bot_token=bot_token, allowed_user_ids=user_ids),
            hub=HubConfig(listen_host="0.0.0.0", listen_port=port),
        )
        write_hub_config(config_path, config)
        self.console.print(f"[bold green]Created hub config:[/] {config_path}")
        self.console.print(f"Hub URL: http://{address}:{port}")
        self.console.print(f"Run: uv run gattv hub --config-path {config_path}")

    def init_camera(self, config_path: Path) -> None:
        self._confirm_overwrite(config_path)
        address = local_ip()
        name = typer.prompt("Camera name", default=socket.gethostname().split(".")[0])
        index = typer.prompt("Webcam index", default=0, type=int)
        rotation = typer.prompt(
            "Clockwise video rotation (0, 90, 180, or 270 degrees)",
            default=0,
            type=int,
        )
        if rotation not in {0, 90, 180, 270}:
            self.console.print(
                "[bold red]Video rotation must be 0, 90, 180, or 270.[/]"
            )
            raise typer.Exit(1)
        port = typer.prompt("Camera port", default=8766, type=int)
        motion_mode = typer.prompt("Motion mode (notify or clip)", default="clip")
        if motion_mode not in {"notify", "clip"}:
            self.console.print("[bold red]Motion mode must be 'notify' or 'clip'.[/]")
            raise typer.Exit(1)
        hub_url = typer.prompt("Hub URL", default=self._suggest_hub_url())
        config = CameraServerConfig.model_validate(
            {
                "listen_host": "0.0.0.0",
                "listen_port": port,
                "hub_url": hub_url,
                "camera": {"name": name, "index": index, "rotation": rotation},
                "motion": {"mode": motion_mode},
            }
        )
        write_camera_config(config_path, config)
        camera_url = f"http://{address}:{port}"
        self.console.print("Waiting for approval at the hub...")
        try:
            asyncio.run(
                register_camera(hub_url, CameraRegistration(name=name, url=camera_url))
            )
        except (aiohttp.ClientError, RuntimeError) as error:
            self.console.print(
                f"[yellow]Config created, but registration failed:[/] {error}"
            )
            self.console.print(
                "Start the hub and run this wizard again to register the camera."
            )
            raise typer.Exit(1) from None
        self.console.print(
            f"[bold green]Created and registered camera config:[/] {config_path}"
        )
        self.console.print(f"Camera URL: {camera_url}")
        self.console.print(f"Run: uv run gattv camera --config-path {config_path}")

    def _suggest_hub_url(self) -> str:
        hubs = discover_hubs()
        if not hubs:
            self.console.print("[yellow]No hub discovered over mDNS.[/]")
            return "http://127.0.0.1:8765"
        self.console.print("Discovered hubs:")
        for position, hub in enumerate(hubs, 1):
            self.console.print(f"  {position}. {hub.name} ({hub.url})")
        choice = typer.prompt("Select hub", default=1, type=int)
        if choice < 1 or choice > len(hubs):
            self.console.print("[bold red]Invalid hub selection.[/]")
            raise typer.Exit(1)
        return hubs[choice - 1].url

    @staticmethod
    def _confirm_overwrite(path: Path) -> None:
        if path.exists() and not typer.confirm(f"Overwrite {path}?", default=False):
            raise typer.Abort()
