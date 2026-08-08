import os
import re
import subprocess
import time
from dataclasses import dataclass

import imageio_ffmpeg


@dataclass(frozen=True)
class HardwareProbeResult:
    backend: str
    device: str
    codec: str | None
    mjpeg_packets: bool
    detail: str
    probe_failed: bool = False
    pixel_format: str | None = None
    width: int | None = None
    height: int | None = None
    input_fps: float | None = None
    packets: int = 0
    packet_bytes: int = 0
    sample_seconds: float = 0
    wall_seconds: float = 0
    cpu_percent: float = 0
    supported_modes: tuple[str, ...] = ()


def probe_ffmpeg(
    command: list[str], backend: str, device: str, sample_seconds: int
) -> HardwareProbeResult:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    child_times = os.times()
    started_at = time.monotonic()
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "verbose", *command],
            capture_output=True,
            text=True,
            check=False,
            timeout=sample_seconds + 15,
        )
    except subprocess.TimeoutExpired:
        return HardwareProbeResult(
            backend,
            device,
            None,
            False,
            "Timed out waiting for a camera frame.",
            probe_failed=True,
        )

    wall_seconds = time.monotonic() - started_at
    completed_child_times = os.times()
    child_cpu_seconds = (
        completed_child_times.children_user
        + completed_child_times.children_system
        - child_times.children_user
        - child_times.children_system
    )
    cpu_percent = child_cpu_seconds / wall_seconds * 100 if wall_seconds else 0
    output = f"{result.stdout}\n{result.stderr}"
    codec = _input_video_codec(output)
    pixel_format = _input_pixel_format(output)
    width, height = _input_resolution(output)
    input_fps = _input_fps(output)
    packets, packet_bytes = _output_packets(output)
    supported_modes = _supported_modes(output)
    if result.returncode != 0:
        return HardwareProbeResult(
            backend,
            device,
            codec,
            False,
            _last_error(output),
            probe_failed=True,
            pixel_format=pixel_format,
            width=width,
            height=height,
            input_fps=input_fps,
            wall_seconds=wall_seconds,
            cpu_percent=cpu_percent,
            supported_modes=supported_modes,
        )

    mjpeg_packets = codec in {"mjpeg", "mjpegb"}
    detail = (
        "The capture backend exposes native MJPEG packets."
        if mjpeg_packets
        else f"The capture backend exposes {codec or 'an unknown codec'}, not MJPEG."
    )
    return HardwareProbeResult(
        backend,
        device,
        codec,
        mjpeg_packets,
        detail,
        pixel_format=pixel_format,
        width=width,
        height=height,
        input_fps=input_fps,
        packets=packets,
        packet_bytes=packet_bytes,
        sample_seconds=sample_seconds,
        wall_seconds=wall_seconds,
        cpu_percent=cpu_percent,
        supported_modes=supported_modes,
    )


def _input_video_codec(output: str) -> str | None:
    match = re.search(r"Video:\s*([a-zA-Z0-9_]+)", _input_video_line(output))
    return match.group(1).lower() if match is not None else None


def _input_pixel_format(output: str) -> str | None:
    for part in _input_video_line(output).split(","):
        value = part.strip().split("(", 1)[0]
        if re.fullmatch(r"(?:yuv|uyvy|yuyv|nv|rgb|bgr|gray)[a-zA-Z0-9_]*", value):
            return value.lower()
    return None


def _input_resolution(output: str) -> tuple[int | None, int | None]:
    match = re.search(r"\b(\d{2,5})x(\d{2,5})\b", _input_video_line(output))
    return (
        (int(match.group(1)), int(match.group(2)))
        if match is not None
        else (None, None)
    )


def _input_fps(output: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s+(?:fps|tbr)\b", _input_video_line(output))
    return float(match.group(1)) if match is not None else None


def _output_packets(output: str) -> tuple[int, int]:
    matches = re.findall(
        r"Output stream .*?:\s*(\d+) packets muxed \((\d+) bytes\)", output
    )
    return (int(matches[-1][0]), int(matches[-1][1])) if matches else (0, 0)


def _supported_modes(output: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"(\d{2,5}x\d{2,5}@\[[^]]+\]fps)", output)))


def _input_video_line(output: str) -> str:
    input_output = output.split("Output #", 1)[0]
    for line in input_output.splitlines():
        if "Stream #" in line and "Video:" in line:
            return line
    return ""


def _last_error(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "FFmpeg could not probe the camera."
