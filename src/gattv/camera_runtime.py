from datetime import datetime
from pathlib import Path
import sys

from aiohttp import web
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from gattv.camera_server import CameraServer
from gattv.config import CameraServerConfig
from gattv.runtime import start_caffeinate, stop_caffeinate
from gattv.setup import local_ip


class CameraRuntime:
    def __init__(
        self, config: CameraServerConfig, config_path: Path, console: Console
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.console = console
        self.server = CameraServer(config)
        self.address = local_ip()

    async def run(self) -> None:
        caffeinate = start_caffeinate(self.console)
        runner = web.AppRunner(self.server.build_application())
        await runner.setup()
        site = web.TCPSite(runner, self.config.listen_host, self.config.listen_port)
        await site.start()
        try:
            with Live(
                self.status_panel(),
                console=self.console,
                auto_refresh=False,
                screen=False,
            ) as live:
                while True:
                    await self.server.motion.state_changed.wait()
                    self.server.motion.state_changed.clear()
                    live.update(self.status_panel(), refresh=True)
        finally:
            await self.server.motion.disarm()
            await runner.cleanup()
            stop_caffeinate(caffeinate)
            self.console.print("[yellow]gattv camera stopped.[/]")

    def status_panel(self) -> Panel:
        camera = self.config.camera
        motion = self.server.motion.state
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()
        table.add_row("Config", str(self.config_path))
        table.add_row("Camera", camera.name)
        table.add_row("URL", f"http://{self.address}:{self.config.listen_port}")
        table.add_row("Hub", self.config.hub_url)
        table.add_row(
            "Capture",
            f"index {camera.index}, {camera.width}x{camera.height} @ {camera.fps} fps",
        )
        table.add_row(
            "Motion",
            f"{'armed' if motion.armed else 'disarmed'}; {motion.status}",
        )
        table.add_row("Motion mode", self.config.motion.mode)
        table.add_row("Last motion", _format_timestamp(motion.last_motion_at))
        table.add_row("Sleep", _sleep_status())
        table.add_row("Stop", "Ctrl+C")
        return Panel(
            table,
            title=f"[bold green]gattv camera: {camera.name}[/]",
            subtitle="Listening for hub requests",
            border_style="green",
        )


def _format_timestamp(value: datetime | None) -> str:
    return "-" if value is None else value.strftime("%Y-%m-%d %H:%M:%S")


def _sleep_status() -> str:
    return (
        "prevented while camera runs" if sys.platform == "darwin" else "system default"
    )
