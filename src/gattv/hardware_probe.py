import re
import subprocess
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


def probe_ffmpeg(command: list[str], backend: str, device: str) -> HardwareProbeResult:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        result = subprocess.run(
            [executable, "-hide_banner", *command],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
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

    output = f"{result.stdout}\n{result.stderr}"
    codec = _input_video_codec(output)
    if result.returncode != 0:
        return HardwareProbeResult(
            backend,
            device,
            codec,
            False,
            _last_error(output),
            probe_failed=True,
        )

    mjpeg_packets = codec in {"mjpeg", "mjpegb"}
    detail = (
        "The capture backend exposes native MJPEG packets."
        if mjpeg_packets
        else f"The capture backend exposes {codec or 'an unknown codec'}, not MJPEG."
    )
    return HardwareProbeResult(backend, device, codec, mjpeg_packets, detail)


def _input_video_codec(output: str) -> str | None:
    input_output = output.split("Output #", 1)[0]
    match = re.search(r"Stream #.*Video:\s*([a-zA-Z0-9_]+)", input_output)
    return match.group(1).lower() if match is not None else None


def _last_error(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "FFmpeg could not probe the camera."
