from pathlib import Path
import subprocess
from subprocess import CompletedProcess
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from rich.console import Console

from gattv.config import CameraConfig
from gattv.hardware_av import (
    AcquisitionMetrics,
    AvFacts,
    StreamFacts,
    build_linux_command,
    build_macos_command,
    inspect_nut,
    parse_benchmark,
    run_av_experiment,
)


def test_macos_command_uses_one_avfoundation_input() -> None:
    command = build_macos_command(
        "ffmpeg", CameraConfig(name="cat"), 180, "0", Path("out.nut")
    )

    assert command[command.index("-f") + 1] == "avfoundation"
    assert "0:0" in command
    assert command[command.index("-framerate") + 1] == "15"
    assert command[command.index("-pixel_format") + 1] == "uyvy422"
    assert command[command.index("-c:v") + 1] == "mjpeg"
    assert ["-threads:v", "1"] == command[
        command.index("-threads:v") : command.index("-threads:v") + 2
    ]
    assert "-nostdin" in command
    assert "-thread_queue_size" in command


def test_linux_command_uses_v4l2_alsa_and_relative_timestamps() -> None:
    command = build_linux_command(
        "ffmpeg", CameraConfig(name="cat"), 180, "hw:2,0", Path("out.nut")
    )

    assert ["-input_format", "mjpeg"] == command[
        command.index("-input_format") : command.index("-input_format") + 2
    ]
    assert ["-timestamps", "mono2abs"] == command[
        command.index("-timestamps") : command.index("-timestamps") + 2
    ]
    assert "hw:2,0" in command
    assert "-copyts" in command
    assert "aresample=async=1" in command
    assert "first_pts" not in " ".join(command)
    assert command.count("-thread_queue_size") == 2
    assert "-flush_packets" in command


def test_parse_benchmark_reports_linux_cpu_and_rss_from_separate_lines() -> None:
    metrics = parse_benchmark(
        "bench: utime=2.00s stime=1.00s rtime=4.00s\nbench: maxrss=10240KiB",
        "linux",
    )

    assert metrics == AcquisitionMetrics(4, 2, 1, 10_485_760)
    assert metrics.cpu_percent == 75


def test_parse_benchmark_treats_darwin_maxrss_as_bytes() -> None:
    metrics = parse_benchmark(
        "bench: utime=2.00s stime=1.00s rtime=4.00s\nbench: maxrss=23248896KiB",
        "darwin",
    )

    assert metrics is not None
    assert metrics.max_rss_bytes == 23_248_896


def test_inspect_nut_demuxes_mixed_streams_once_and_includes_video_duration() -> None:
    video = SimpleNamespace(type="video", time_base=Fraction(1, 10), average_rate=10)
    audio = SimpleNamespace(
        type="audio", time_base=Fraction(1, 48000), average_rate=None
    )
    video.codec_context = Mock(decode=Mock(return_value=[]))
    audio.codec_context = Mock(decode=Mock(return_value=[]))
    video_frame = SimpleNamespace(pts=10, time_base=Fraction(1, 10), duration=1)
    audio_frame = SimpleNamespace(
        pts=24000, time_base=Fraction(1, 48000), samples=480, sample_rate=48000
    )
    packets = [
        SimpleNamespace(stream=video, decode=Mock(return_value=[video_frame])),
        SimpleNamespace(stream=audio, decode=Mock(return_value=[audio_frame])),
    ]
    container = MagicMock(streams=[video, audio])
    container.demux.return_value = packets
    container.__enter__ = Mock(return_value=container)
    container.__exit__ = Mock(return_value=None)

    with patch("gattv.hardware_av.av.open", return_value=container):
        facts = inspect_nut(Path("capture.nut"))

    container.demux.assert_called_once_with([video, audio])
    assert facts.video == StreamFacts(1, 1.1, 1, 1, 0)
    assert facts.audio == StreamFacts(0.5, 0.51, 1, 1, 480)


def test_success_converts_output_and_removes_temporary_artifact(tmp_path: Path) -> None:
    output = tmp_path / "result.mp4"
    facts = AvFacts(
        StreamFacts(0, 10, 150, 150, 0), StreamFacts(0, 10, 470, 470, 480000)
    )
    nut_paths: list[Path] = []

    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        destination = Path(command[-1])
        if destination.suffix == ".nut":
            nut_paths.append(destination)
            destination.write_bytes(b"nut")
            return CompletedProcess(
                command,
                0,
                "",
                "bench: utime=1s stime=1s rtime=2s\nbench: maxrss=100KiB",
            )
        destination.write_bytes(b"mp4")
        return CompletedProcess(command, 0, "", "")

    console = Console(record=True)
    with (
        patch("gattv.hardware_av.sys.platform", "darwin"),
        patch("gattv.hardware_av.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
        patch("gattv.hardware_av.subprocess.run", side_effect=run),
        patch("gattv.hardware_av.inspect_nut", return_value=facts),
    ):
        assert run_av_experiment(
            CameraConfig(name="cat"), 10, 20, None, output, console
        )

    assert output.read_bytes() == b"mp4"
    assert not nut_paths[0].exists()
    assert "Estimated retained buffer" in console.export_text()


def test_acquisition_timeout_reports_and_cleans_temporary_data(tmp_path: Path) -> None:
    console = Console(record=True)
    with (
        patch("gattv.hardware_av.sys.platform", "darwin"),
        patch("gattv.hardware_av.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
        patch(
            "gattv.hardware_av.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ffmpeg", 35),
        ) as run,
    ):
        assert not run_av_experiment(
            CameraConfig(name="cat"), 10, 20, None, tmp_path / "out.mp4", console
        )

    assert run.call_args.kwargs["timeout"] == 35
    assert "acquisition timed out" in console.export_text().lower()


def test_conversion_timeout_reports_and_cleans_temporary_data(tmp_path: Path) -> None:
    console = Console(record=True)
    nut_paths: list[Path] = []
    facts = AvFacts(StreamFacts(0, 1, 1, 1, 0), StreamFacts(0, 1, 1, 1, 48000))

    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        destination = Path(command[-1])
        if destination.suffix == ".nut":
            nut_paths.append(destination)
            destination.write_bytes(b"nut")
            return CompletedProcess(
                command,
                0,
                "",
                "bench: utime=1s stime=1s rtime=2s\nbench: maxrss=100KiB",
            )
        raise subprocess.TimeoutExpired("ffmpeg", 40)

    with (
        patch("gattv.hardware_av.sys.platform", "darwin"),
        patch("gattv.hardware_av.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
        patch("gattv.hardware_av.subprocess.run", side_effect=run) as subprocess_run,
        patch("gattv.hardware_av.inspect_nut", return_value=facts),
    ):
        assert not run_av_experiment(
            CameraConfig(name="cat"), 10, 20, None, tmp_path / "out.mp4", console
        )

    assert subprocess_run.call_args.kwargs["timeout"] == 40
    assert not nut_paths[0].exists()
    assert "mp4 conversion timed out" in console.export_text().lower()


def test_linux_busy_audio_has_rerun_guidance(tmp_path: Path) -> None:
    console = Console(record=True)
    failed = Mock(returncode=1, stdout="", stderr="ALSA: Device or resource busy")
    with (
        patch("gattv.hardware_av.sys.platform", "linux"),
        patch("gattv.hardware_av.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
        patch("gattv.hardware_av.subprocess.run", return_value=failed),
    ):
        assert not run_av_experiment(
            CameraConfig(name="cat"), 10, 20, None, tmp_path / "out.mp4", console
        )

    assert "--audio-device default" in console.export_text()


def test_censored_failure_omits_ffmpeg_paths_and_hostnames(tmp_path: Path) -> None:
    console = Console(record=True)
    failed = Mock(
        returncode=1,
        stdout="",
        stderr="alice@laptop.example.com cannot open /Users/alice/gattv/capture.nut",
    )
    with (
        patch("gattv.hardware_av.sys.platform", "darwin"),
        patch(
            "gattv.hardware_av.imageio_ffmpeg.get_ffmpeg_exe",
            return_value="/Users/alice/bin/ffmpeg",
        ),
        patch("gattv.hardware_av.subprocess.run", return_value=failed),
    ):
        assert not run_av_experiment(
            CameraConfig(name="alice-camera"),
            10,
            20,
            None,
            tmp_path / "alice-sync.mp4",
            console,
            censor=True,
        )

    output = console.export_text()
    for sensitive in ("alice", "laptop.example.com", "/Users", "capture.nut"):
        assert sensitive not in output
    assert "raw device/log output omitted by --censor" in output


def test_censored_success_keeps_metrics_and_redacts_output_path(tmp_path: Path) -> None:
    output = tmp_path / "sync.mp4"
    facts = AvFacts(
        StreamFacts(0, 10, 150, 150, 0), StreamFacts(0, 10, 470, 470, 480000)
    )

    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        destination = Path(command[-1])
        if destination.suffix == ".nut":
            destination.write_bytes(b"nut")
            return CompletedProcess(
                command,
                0,
                "",
                "bench: utime=1s stime=1s rtime=2s\n"
                "bench: maxrss=100KiB\nwarning alice@laptop.example.com /Users/alice",
            )
        destination.write_bytes(b"mp4")
        return CompletedProcess(command, 0, "", "")

    console = Console(record=True)
    with (
        patch("gattv.hardware_av.sys.platform", "darwin"),
        patch(
            "gattv.hardware_av.imageio_ffmpeg.get_ffmpeg_exe",
            return_value="/Users/alice/bin/ffmpeg",
        ),
        patch("gattv.hardware_av.subprocess.run", side_effect=run),
        patch("gattv.hardware_av.inspect_nut", return_value=facts),
    ):
        assert run_av_experiment(
            CameraConfig(name="alice-camera"),
            10,
            20,
            None,
            output,
            console,
            censor=True,
        )

    rendered = console.export_text()
    for sensitive in ("alice", "laptop.example.com", str(tmp_path)):
        assert sensitive not in rendered
    assert "<output>/sync.mp4" in rendered
    assert "Acquisition elapsed / CPU" in rendered
    assert "Audio − video end delta" in rendered
