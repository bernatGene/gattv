from types import SimpleNamespace
from unittest.mock import Mock, patch

from rich.console import Console

from gattv.config import CameraConfig
from gattv.hardware import _probe_camera, test_camera_hardware as run_hardware_test
from gattv.hardware_probe import HardwareProbeResult, probe_ffmpeg


def test_probe_ffmpeg_detects_native_mjpeg() -> None:
    process = Mock(
        returncode=0,
        stdout="",
        stderr=(
            "Stream #0:0: Video: mjpeg, yuvj422p, 1280x720, 15 fps\n"
            "Output stream #0:0 (video): 75 packets muxed (7500000 bytes)\n"
        ),
    )

    with (
        patch(
            "gattv.hardware_probe.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"
        ),
        patch("gattv.hardware_probe.subprocess.run", return_value=process),
    ):
        result = probe_ffmpeg(["-i", "camera"], "backend", "camera", 5)

    assert result.mjpeg_packets is True
    assert result.codec == "mjpeg"
    assert result.pixel_format == "yuvj422p"
    assert result.input_fps == 15
    assert result.packets == 75
    assert result.packet_bytes == 7_500_000


def test_probe_ffmpeg_rejects_decoded_video() -> None:
    process = Mock(
        returncode=0,
        stdout="",
        stderr="Stream #0:0: Video: rawvideo, uyvy422, 1280x720, 15 fps\n",
    )

    with (
        patch(
            "gattv.hardware_probe.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"
        ),
        patch("gattv.hardware_probe.subprocess.run", return_value=process),
    ):
        result = probe_ffmpeg(["-i", "camera"], "backend", "camera", 5)

    assert result.mjpeg_packets is False
    assert result.codec == "rawvideo"


def test_probe_camera_imports_only_current_platform_module() -> None:
    camera = CameraConfig(name="test")
    expected = HardwareProbeResult("AVFoundation", "camera", "mjpeg", True, "ok")
    module = SimpleNamespace(probe_mjpeg=Mock(return_value=expected))

    with (
        patch("gattv.hardware.sys.platform", "darwin"),
        patch(
            "gattv.hardware.importlib.import_module", return_value=module
        ) as import_module,
    ):
        result = _probe_camera(camera, 5)

    assert result == expected
    import_module.assert_called_once_with("gattv.hardware_macos")
    module.probe_mjpeg.assert_called_once_with(camera, 5)


def test_hardware_test_reports_success() -> None:
    camera = CameraConfig(name="test")
    result = HardwareProbeResult("backend", "camera", "mjpeg", True, "supported")
    console = Console(record=True)

    with patch("gattv.hardware._probe_camera", return_value=result):
        supported = run_hardware_test(camera, 10, 5, console)

    output = console.export_text()
    assert supported is True
    assert "Native MJPEG packets" in output
    assert "yes" in output


def test_hardware_test_succeeds_without_mjpeg() -> None:
    camera = CameraConfig(name="test")
    result = HardwareProbeResult("backend", "camera", "rawvideo", False, "raw")
    console = Console()

    with patch("gattv.hardware._probe_camera", return_value=result):
        supported = run_hardware_test(camera, 10, 5, console)

    assert supported is True


def test_hardware_test_reports_audio_diagnostics_on_all_platforms() -> None:
    camera = CameraConfig(name="test")
    result = HardwareProbeResult("backend", "camera", "mjpeg", True, "supported")
    console = Console()

    with (
        patch("gattv.hardware._probe_camera", return_value=result),
        patch("gattv.hardware.report_audio_hardware") as report_audio,
    ):
        supported = run_hardware_test(camera, 10, 5, console)

    assert supported is True
    report_audio.assert_called_once_with(camera, console)
