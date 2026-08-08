import importlib
import sys
from collections.abc import Callable
from typing import cast

from rich.console import Console
from rich.table import Table

from gattv.config import CameraConfig
from gattv.hardware_probe import HardwareProbeResult


Probe = Callable[[CameraConfig, int], HardwareProbeResult]


def test_camera_hardware(
    camera: CameraConfig,
    buffer_seconds: int,
    sample_seconds: int,
    console: Console,
) -> bool:
    result = _probe_camera(camera, sample_seconds)
    table = Table(title="Camera hardware test")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("Camera", camera.name)
    table.add_row("Backend", result.backend)
    table.add_row("Device", result.device)
    table.add_row(
        "Configured mode", f"{camera.width}x{camera.height} @ {camera.fps} fps"
    )
    if result.width is not None and result.height is not None:
        active_fps = f" @ {result.input_fps:g} fps" if result.input_fps else ""
        table.add_row("Active mode", f"{result.width}x{result.height}{active_fps}")
    if result.supported_modes:
        table.add_row("Supported modes", "\n".join(result.supported_modes))
    table.add_row("Input codec", result.codec or "unknown")
    table.add_row("Pixel format", result.pixel_format or "unknown")
    table.add_row(
        "Native MJPEG packets",
        "[bold green]yes[/]" if result.mjpeg_packets else "[bold red]no[/]",
    )
    if result.sample_seconds:
        measured_fps = result.packets / result.sample_seconds
        data_rate = result.packet_bytes / result.sample_seconds
        table.add_row(
            "Sample", f"{result.sample_seconds:g} s, {result.packets} packets"
        )
        table.add_row("Measured packet rate", f"{measured_fps:.2f} packets/s")
        table.add_row("Stream data rate", f"{data_rate / 1024 / 1024:.2f} MiB/s")
        table.add_row("Capture CPU", f"{result.cpu_percent:.1f}% of one core")
        table.add_row(
            f"Stream buffer ({buffer_seconds} s)",
            _format_mib(data_rate * buffer_seconds),
        )
    sampled_bytes = _sampled_native_buffer_bytes(camera, result, buffer_seconds)
    if sampled_bytes is not None and not result.mjpeg_packets:
        table.add_row(
            f"Sampled native buffer ({buffer_seconds} s)",
            _format_mib(sampled_bytes),
        )
    width = result.width or camera.width
    height = result.height or camera.height
    bgr_bytes = width * height * 3 * camera.fps * buffer_seconds
    table.add_row(f"BGR buffer ({buffer_seconds} s)", _format_mib(bgr_bytes))
    table.add_row("Suggested strategy", _suggested_strategy(result))
    table.add_row("Result", result.detail)
    console.print(table)
    return not result.probe_failed


def _probe_camera(camera: CameraConfig, sample_seconds: int) -> HardwareProbeResult:
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
    return probe(camera, sample_seconds)


def _sampled_native_buffer_bytes(
    camera: CameraConfig, result: HardwareProbeResult, buffer_seconds: int
) -> float | None:
    bytes_per_pixel = {
        "uyvy422": 2,
        "yuyv422": 2,
        "nv12": 1.5,
        "yuv420p": 1.5,
        "bgr0": 4,
        "0rgb": 4,
        "rgb24": 3,
        "bgr24": 3,
        "gray": 1,
        "gray8": 1,
    }.get(result.pixel_format or "")
    if bytes_per_pixel is None:
        return None
    width = result.width or camera.width
    height = result.height or camera.height
    return width * height * bytes_per_pixel * camera.fps * buffer_seconds


def _suggested_strategy(result: HardwareProbeResult) -> str:
    if result.probe_failed:
        return "Resolve the probe error before choosing a buffer."
    if result.mjpeg_packets:
        return "Timestamped native MJPEG packet buffer"
    if result.pixel_format:
        return f"Timestamped {result.pixel_format} frames sampled at configured FPS"
    return "Timestamped raw frame buffer"


def _format_mib(byte_count: float) -> str:
    return f"{byte_count / 1024 / 1024:.1f} MiB"
