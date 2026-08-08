from dataclasses import replace

from gattv.config import CameraConfig
from gattv.hardware_probe import HardwareProbeResult, probe_ffmpeg


def probe_mjpeg(camera: CameraConfig) -> HardwareProbeResult:
    device = f"video device {camera.index}"
    result = _probe_at_fps(camera, device, camera.fps)
    if not result.probe_failed or camera.fps == 30:
        return result

    fallback = _probe_at_fps(camera, device, 30)
    if fallback.probe_failed:
        return result
    return replace(
        fallback,
        detail=f"AVFoundation was probed at 30 fps. {fallback.detail}",
    )


def _probe_at_fps(camera: CameraConfig, device: str, fps: int) -> HardwareProbeResult:
    return probe_ffmpeg(
        [
            "-f",
            "avfoundation",
            "-framerate",
            str(fps),
            "-video_size",
            f"{camera.width}x{camera.height}",
            "-i",
            f"{camera.index}:none",
            "-frames:v",
            "1",
            "-c:v",
            "copy",
            "-f",
            "null",
            "-",
        ],
        "AVFoundation",
        device,
    )
