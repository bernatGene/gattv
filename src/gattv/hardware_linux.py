from gattv.config import CameraConfig
from gattv.hardware_probe import HardwareProbeResult, probe_ffmpeg


def probe_mjpeg(camera: CameraConfig) -> HardwareProbeResult:
    device = f"/dev/video{camera.index}"
    return probe_ffmpeg(
        [
            "-f",
            "v4l2",
            "-input_format",
            "mjpeg",
            "-video_size",
            f"{camera.width}x{camera.height}",
            "-framerate",
            str(camera.fps),
            "-i",
            device,
            "-frames:v",
            "1",
            "-c:v",
            "copy",
            "-f",
            "null",
            "-",
        ],
        "Video4Linux2",
        device,
    )
