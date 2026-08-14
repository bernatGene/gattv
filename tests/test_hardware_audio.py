from unittest.mock import Mock, patch

from rich.console import Console

from gattv.config import CameraConfig
from gattv.hardware_audio import (
    CommandResult,
    _input_devices,
    _run_command,
    report_audio_hardware,
)


def test_input_devices_only_includes_input_backends() -> None:
    result = CommandResult(" DE alsa            ALSA audio input\n E  als")

    assert _input_devices(result) == {"alsa"}


def test_device_listing_keeps_stderr_when_nonzero_is_expected() -> None:
    process = Mock(
        returncode=1, stdout="", stderr="[AVFoundation indev] [1] Microphone"
    )

    with patch("gattv.hardware_audio.subprocess.run", return_value=process):
        result = _run_command(["ffmpeg"], allow_nonzero=True)

    assert result.error is None
    assert result.returncode == 1
    assert result.output == "[AVFoundation indev] [1] Microphone"


def test_linux_audio_report_includes_backend_and_device_facts() -> None:
    results = iter(
        [
            CommandResult("ffmpeg version 7.1"),
            CommandResult(" DE alsa            ALSA audio input\n D  pipewire"),
            CommandResult("arecord: version 1.2.11"),
            CommandResult("pw-record 1.2.8"),
            CommandResult("wpctl 1.2.8"),
            CommandResult("card 1: USB [USB Microphone], device 0"),
            CommandResult("Audio\n ├─ Sources:\n │  * 42. USB Microphone"),
        ]
    )
    console = Console(record=True, width=120)

    with (
        patch("gattv.hardware_audio.sys.platform", "linux"),
        patch(
            "gattv.hardware_audio.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"
        ),
        patch("gattv.hardware_audio._run_command", side_effect=results) as run_command,
        patch("gattv.hardware_audio.av") as av,
    ):
        av.__version__ = "17.1.0"
        av.formats_available = {"alsa"}
        report_audio_hardware(CameraConfig(name="test"), console)

    output = console.export_text()
    assert "FFmpeg binary" in output
    assert "FFmpeg ALSA input" in output
    assert "FFmpeg PipeWire input" in output
    assert "PyAV ALSA input" in output
    assert "PyAV PipeWire input" in output
    assert "arecord" in output
    assert "pw-record" in output
    assert "ALSA capture devices: arecord -l" in output
    assert "USB Microphone" in output
    assert "PipeWire audio nodes: wpctl status --name" in output
    assert run_command.call_args_list[1].args[0] == [
        "ffmpeg",
        "-hide_banner",
        "-devices",
    ]


def test_macos_audio_report_keeps_nonzero_avfoundation_device_listing() -> None:
    results = iter(
        [
            CommandResult("ffmpeg version 7.1"),
            CommandResult(" D  avfoundation   AVFoundation input device"),
            CommandResult(
                "[AVFoundation indev] AVFoundation video devices:\n"
                "[AVFoundation indev] [2] FaceTime HD Camera\n"
                "[AVFoundation indev] AVFoundation audio devices:\n"
                "[AVFoundation indev] [1] MacBook Pro Microphone",
                returncode=1,
            ),
        ]
    )
    console = Console(record=True, width=120)

    with (
        patch("gattv.hardware_audio.sys.platform", "darwin"),
        patch(
            "gattv.hardware_audio.imageio_ffmpeg.get_ffmpeg_exe",
            return_value="/tmp/ffmpeg",
        ),
        patch("gattv.hardware_audio._run_command", side_effect=results) as run_command,
        patch("gattv.hardware_audio.av") as av,
    ):
        av.__version__ = "17.1.0"
        av.formats_available = {"avfoundation"}
        report_audio_hardware(CameraConfig(name="test", index=2), console)

    output = console.export_text()
    assert "FFmpeg AVFoundation input" in output
    assert "PyAV AVFoundation input" in output
    assert "Configured AVFoundation pair" in output
    assert "2:<audio device index>" in output
    assert "FaceTime HD Camera" in output
    assert "MacBook Pro Microphone" in output
    assert "Exited 1 after listing devices." in output
    assert run_command.call_args_list[1].args[0] == [
        "/tmp/ffmpeg",
        "-hide_banner",
        "-devices",
    ]
    assert run_command.call_args.kwargs == {"allow_nonzero": True}


def test_unsupported_platform_audio_report_is_explicit() -> None:
    console = Console(record=True)

    with (
        patch("gattv.hardware_audio.sys.platform", "win32"),
        patch(
            "gattv.hardware_audio.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"
        ),
        patch(
            "gattv.hardware_audio._run_command",
            return_value=CommandResult("ffmpeg version 7.1"),
        ),
        patch("gattv.hardware_audio.av") as av,
    ):
        av.__version__ = "17.1.0"
        report_audio_hardware(CameraConfig(name="test"), console)

    assert "Not supported on this platform." in console.export_text()
