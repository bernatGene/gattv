import importlib
import sys
from collections.abc import Callable
from typing import cast

from rich.console import Console
from rich.table import Table

from gattv.config import CameraConfig
from gattv.hardware_probe import HardwareProbeResult


Probe = Callable[[CameraConfig], HardwareProbeResult]


def test_camera_hardware(camera: CameraConfig, console: Console) -> bool:
    result = _probe_camera(camera)
    table = Table(title="Camera hardware test")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("Camera", camera.name)
    table.add_row("Backend", result.backend)
    table.add_row("Device", result.device)
    table.add_row(
        "Configured mode", f"{camera.width}x{camera.height} @ {camera.fps} fps"
    )
    table.add_row("Input codec", result.codec or "unknown")
    table.add_row(
        "Native MJPEG packets",
        "[bold green]yes[/]" if result.mjpeg_packets else "[bold red]no[/]",
    )
    table.add_row("Result", result.detail)
    console.print(table)
    return result.mjpeg_packets and not result.probe_failed


def _probe_camera(camera: CameraConfig) -> HardwareProbeResult:
    module_name = {
        "darwin": "gattv.hardware_macos",
        "linux": "gattv.hardware_linux",
    }.get(sys.platform)
    if module_name is None:
        return HardwareProbeResult(
            sys.platform,
            str(camera.index),
            None,
            False,
            f"Hardware probing is not supported on {sys.platform}.",
            probe_failed=True,
        )

    module = importlib.import_module(module_name)
    probe = cast(Probe, module.probe_mjpeg)
    return probe(camera)
