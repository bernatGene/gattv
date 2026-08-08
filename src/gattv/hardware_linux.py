from gattv.config import CameraConfig
from gattv.hardware_probe import HardwareProbeResult, probe_ffmpeg


def probe_mjpeg(camera: CameraConfig, sample_seconds: int) -> HardwareProbeResult:
    device = f"/dev/video{camera.index}"
    result = _probe(camera, device, sample_seconds, "mjpeg")
    if not result.probe_failed:
        return result
    return _probe(camera, device, sample_seconds, None)


def _probe(
    camera: CameraConfig,
    device: str,
    sample_seconds: int,
    input_format: str | None,
) -> HardwareProbeResult:
    input_options = ["-input_format", input_format] if input_format is not None else []
    return probe_ffmpeg(
        [
            "-f",
            "v4l2",
            *input_options,
            "-video_size",
            f"{camera.width}x{camera.height}",
            "-framerate",
            str(camera.fps),
            "-i",
            device,
            "-t",
            str(sample_seconds),
            "-c:v",
            "copy",
            "-f",
            "null",
            "-",
        ],
        "Video4Linux2",
        device,
        sample_seconds,
    )
