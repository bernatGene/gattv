import subprocess
import sys
from dataclasses import dataclass

import av
import imageio_ffmpeg
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gattv.config import CameraConfig
from gattv.hardware_report import ReportRedaction


COMMAND_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class CommandResult:
    output: str
    error: str | None = None
    returncode: int = 0


def report_audio_hardware(
    camera: CameraConfig, console: Console, censor: bool = False
) -> None:
    report = ReportRedaction(censor)
    executable, ffmpeg_version, ffmpeg_devices = _probe_ffmpeg()
    table = Table(title="Audio hardware test")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("Platform", sys.platform)
    table.add_row("FFmpeg binary", report.executable(executable))
    table.add_row("FFmpeg version", report.version(ffmpeg_version))
    table.add_row("PyAV version", av.__version__)

    if sys.platform == "darwin":
        _report_macos_audio(camera, console, table, executable, ffmpeg_devices, report)
    elif sys.platform == "linux":
        _report_linux_audio(console, table, ffmpeg_devices, report)
    else:
        table.add_row("Audio device discovery", "Not supported on this platform.")
        console.print(table)


def _report_macos_audio(
    camera: CameraConfig,
    console: Console,
    table: Table,
    executable: str,
    ffmpeg_devices: CommandResult,
    report: ReportRedaction,
) -> None:
    table.add_row(
        "FFmpeg AVFoundation input",
        _ffmpeg_input_device_available(ffmpeg_devices, "avfoundation", report),
    )
    table.add_row(
        "PyAV AVFoundation input",
        _format_available(av.formats_available, "avfoundation"),
    )
    table.add_row(
        "Configured AVFoundation pair", f"{camera.index}:<audio device index>"
    )
    console.print(table)
    _print_devices(
        console,
        "AVFoundation devices: ffmpeg -f avfoundation -list_devices true -i ''",
        [
            executable,
            "-hide_banner",
            "-f",
            "avfoundation",
            "-list_devices",
            "true",
            "-i",
            "",
        ],
        nonzero_is_listing=True,
        report=report,
    )


def _report_linux_audio(
    console: Console,
    table: Table,
    ffmpeg_devices: CommandResult,
    report: ReportRedaction,
) -> None:
    table.add_row(
        "FFmpeg ALSA input",
        _ffmpeg_input_device_available(ffmpeg_devices, "alsa", report),
    )
    table.add_row(
        "FFmpeg PipeWire input",
        _ffmpeg_input_device_available(ffmpeg_devices, "pipewire", report),
    )
    table.add_row("PyAV ALSA input", _format_available(av.formats_available, "alsa"))
    table.add_row(
        "PyAV PipeWire input", _format_available(av.formats_available, "pipewire")
    )
    table.add_row("arecord", report.version(_command_version(["arecord", "--version"])))
    table.add_row(
        "pw-record", report.version(_command_version(["pw-record", "--version"]))
    )
    table.add_row("wpctl", report.version(_command_version(["wpctl", "--version"])))
    console.print(table)
    _print_devices(
        console, "ALSA capture devices: arecord -l", ["arecord", "-l"], report=report
    )
    _print_devices(
        console,
        "PipeWire audio nodes: wpctl status --name",
        ["wpctl", "status", "--name"],
        report=report,
    )


def _probe_ffmpeg() -> tuple[str, str, CommandResult]:
    try:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError as error:
        unavailable = CommandResult("", f"Unavailable: {error}")
        return "unavailable", unavailable.error, unavailable
    version = _run_command([executable, "-hide_banner", "-version"])
    devices = _run_command([executable, "-hide_banner", "-devices"])
    return executable, version.error or _first_line(version.output), devices


def _input_devices(result: CommandResult) -> set[str]:
    if result.error is not None:
        return set()
    devices = set()
    for line in result.output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and "D" in fields[0]:
            devices.add(fields[1])
    return devices


def _format_available(formats: set[str], name: str) -> str:
    return "available" if name in formats else "not available"


def _ffmpeg_input_device_available(
    result: CommandResult, name: str, report: ReportRedaction | None = None
) -> str:
    if result.error is not None:
        return f"unknown ({(report or ReportRedaction()).availability_error(result.error)})"
    return _format_available(_input_devices(result), name)


def _command_version(command: list[str]) -> str:
    result = _run_command(command)
    return result.error or _first_line(result.output)


def _print_devices(
    console: Console,
    title: str,
    command: list[str],
    nonzero_is_listing: bool = False,
    report: ReportRedaction | None = None,
) -> None:
    policy = report or ReportRedaction()
    if policy.censor:
        console.print(Panel(Text(policy.listing_message()), title=title))
        return
    result = _run_command(command, allow_nonzero=nonzero_is_listing)
    output = result.error or result.output.strip() or "No devices reported."
    if nonzero_is_listing and result.error is None and result.returncode != 0:
        output = f"Exited {result.returncode} after listing devices.\n{output}"
    console.print(Panel(Text(output), title=title))


def _run_command(command: list[str], allow_nonzero: bool = False) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return CommandResult("", f"Not found: {command[0]}")
    except subprocess.TimeoutExpired:
        return CommandResult("", f"Timed out after {COMMAND_TIMEOUT_SECONDS} seconds")
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0 and not allow_nonzero:
        return CommandResult("", f"Exited {result.returncode}: {_first_line(output)}")
    return CommandResult(output, returncode=result.returncode)


def _first_line(output: str) -> str:
    return output.splitlines()[0] if output else "No output"
