import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import av
import imageio_ffmpeg
from rich.console import Console
from rich.table import Table

from gattv.config import CameraConfig
from gattv.hardware_report import ReportRedaction


CPU_PATTERN = re.compile(
    r"utime=(?P<user>[0-9.]+)s\s+stime=(?P<system>[0-9.]+)s\s+"
    r"rtime=(?P<elapsed>[0-9.]+)s"
)
RSS_PATTERN = re.compile(r"maxrss=(?P<rss>[0-9]+)ki?b", re.IGNORECASE)
ACQUISITION_GRACE_SECONDS = 15
CONVERSION_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class AcquisitionMetrics:
    elapsed_seconds: float
    user_seconds: float
    system_seconds: float
    max_rss_bytes: int

    @property
    def cpu_percent(self) -> float:
        if self.elapsed_seconds == 0:
            return 0
        return 100 * (self.user_seconds + self.system_seconds) / self.elapsed_seconds


@dataclass(frozen=True)
class StreamFacts:
    first_seconds: float | None
    end_seconds: float | None
    packets: int
    frames: int
    samples: int


@dataclass(frozen=True)
class AvFacts:
    video: StreamFacts
    audio: StreamFacts


@dataclass
class _StreamAccumulator:
    first_seconds: float | None = None
    end_seconds: float | None = None
    packets: int = 0
    frames: int = 0
    samples: int = 0

    def facts(self) -> StreamFacts:
        return StreamFacts(
            self.first_seconds,
            self.end_seconds,
            self.packets,
            self.frames,
            self.samples,
        )


def build_macos_command(
    executable: str,
    camera: CameraConfig,
    seconds: int,
    audio_device: str,
    output: Path,
    input_fps: int | None = None,
) -> list[str]:
    capture_fps = input_fps or camera.fps
    video_filter = ["-vf", f"fps={camera.fps}"] if capture_fps != camera.fps else []
    return [
        executable,
        "-hide_banner",
        "-nostdin",
        "-benchmark",
        "-y",
        "-thread_queue_size",
        "64",
        "-f",
        "avfoundation",
        "-framerate",
        str(capture_fps),
        "-video_size",
        f"{camera.width}x{camera.height}",
        "-pixel_format",
        "uyvy422",
        "-i",
        f"{camera.index}:{audio_device}",
        "-t",
        str(seconds),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        *video_filter,
        "-c:v",
        "mjpeg",
        "-q:v",
        "7",
        "-threads:v",
        "1",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-flush_packets",
        "1",
        "-max_interleave_delta",
        "0",
        "-f",
        "nut",
        str(output),
    ]


def build_linux_command(
    executable: str, camera: CameraConfig, seconds: int, audio_device: str, output: Path
) -> list[str]:
    return [
        executable,
        "-hide_banner",
        "-nostdin",
        "-benchmark",
        "-y",
        "-thread_queue_size",
        "64",
        "-f",
        "v4l2",
        "-input_format",
        "mjpeg",
        "-video_size",
        f"{camera.width}x{camera.height}",
        "-framerate",
        str(camera.fps),
        "-timestamps",
        "mono2abs",
        "-i",
        f"/dev/video{camera.index}",
        "-thread_queue_size",
        "64",
        "-f",
        "alsa",
        "-i",
        audio_device,
        "-t",
        str(seconds),
        "-copyts",
        "-start_at_zero",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-af",
        "aresample=async=1",
        "-flush_packets",
        "1",
        "-max_interleave_delta",
        "0",
        "-f",
        "nut",
        str(output),
    ]


def parse_benchmark(output: str, platform: str) -> AcquisitionMetrics | None:
    cpu_matches = list(CPU_PATTERN.finditer(output))
    rss_matches = list(RSS_PATTERN.finditer(output))
    if not cpu_matches or not rss_matches:
        return None
    cpu = cpu_matches[-1].groupdict()
    rss = int(rss_matches[-1].group("rss"))
    rss_bytes = rss if platform == "darwin" else rss * 1024
    return AcquisitionMetrics(
        float(cpu["elapsed"]), float(cpu["user"]), float(cpu["system"]), rss_bytes
    )


def run_av_experiment(
    camera: CameraConfig,
    buffer_seconds: int,
    seconds: int,
    audio_device: str | None,
    output_path: Path,
    console: Console,
    censor: bool = False,
) -> bool:
    report = ReportRedaction(censor)
    platform = sys.platform
    if platform not in {"darwin", "linux"}:
        console.print(f"[yellow]A/V experiment is not supported on {platform}.[/]")
        return False
    device = audio_device or ("0" if platform == "darwin" else "hw:0,0")
    try:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError as error:
        detail = "<redacted>" if censor else str(error)
        console.print(
            f"[bold red]A/V experiment could not resolve imageio_ffmpeg:[/] {detail}"
        )
        return False

    console.print(
        f"[bold]A/V acquisition ({seconds} s):[/] clap visibly near the beginning and again near the end."
    )
    output_path = output_path.resolve()
    with tempfile.TemporaryDirectory(prefix="gattv-av-") as directory:
        nut_path = Path(directory) / "capture.nut"
        command = (
            build_macos_command(executable, camera, seconds, device, nut_path)
            if platform == "darwin"
            else build_linux_command(executable, camera, seconds, device, nut_path)
        )
        capture = _run_ffmpeg(
            command, seconds + ACQUISITION_GRACE_SECONDS, console, "acquisition", report
        )
        if capture is None:
            return False
        capture_log = "\n".join((capture.stdout, capture.stderr))
        should_retry_macos = (
            platform == "darwin"
            and camera.fps != 30
            and (capture.returncode != 0 or not nut_path.exists())
        )
        if should_retry_macos:
            nut_path.unlink(missing_ok=True)
            console.print(
                f"[yellow]Configured {camera.fps} fps acquisition was unavailable; "
                f"testing 30 -> {camera.fps} fps fallback.[/]"
            )
            capture = _run_ffmpeg(
                build_macos_command(
                    executable, camera, seconds, device, nut_path, input_fps=30
                ),
                seconds + ACQUISITION_GRACE_SECONDS,
                console,
                "acquisition",
                report,
            )
            if capture is None:
                return False
            capture_log = "\n".join((capture.stdout, capture.stderr))
        if capture.returncode != 0:
            _report_failure(
                console, capture_log, platform, device, "acquisition", report
            )
            return False
        metrics = parse_benchmark(capture_log, platform)
        if not nut_path.exists():
            console.print(
                "[bold red]A/V acquisition failed:[/] FFmpeg did not create the NUT artifact."
            )
            return False
        nut_bytes = nut_path.stat().st_size
        try:
            facts = inspect_nut(nut_path)
        except av.FFmpegError as error:
            detail = "<redacted>" if censor else str(error)
            console.print(
                f"[bold red]A/V validation failed while decoding NUT:[/] {detail}"
            )
            return False
        if facts.video.frames == 0 or facts.audio.frames == 0:
            console.print(
                "[bold red]A/V validation failed:[/] NUT did not decode both audio and video."
            )
            return False
        conversion = _run_ffmpeg(
            _mp4_command(executable, nut_path, output_path),
            min(CONVERSION_TIMEOUT_SECONDS, max(30, seconds * 2)),
            console,
            "MP4 conversion",
            report,
        )
        if conversion is None:
            return False
        if conversion.returncode != 0:
            _report_failure(
                console,
                "\n".join((conversion.stdout, conversion.stderr)),
                platform,
                device,
                "MP4 encoding",
                report,
            )
            return False
    _report_success(
        console,
        metrics,
        facts,
        nut_bytes,
        buffer_seconds,
        seconds,
        output_path,
        report,
        _relevant_warnings(capture_log),
    )
    return True


def _mp4_command(executable: str, nut_path: Path, output_path: Path) -> list[str]:
    return [
        executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(nut_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _run_ffmpeg(
    command: list[str],
    timeout: int,
    console: Console,
    phase: str,
    report: ReportRedaction,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        console.print(
            f"[bold red]{phase} timed out:[/] FFmpeg was stopped after {timeout} seconds."
        )
        return None
    except OSError as error:
        detail = "<redacted>" if report.censor else str(error)
        console.print(f"[bold red]{phase} device opening failed:[/] {detail}")
        return None


def inspect_nut(path: Path) -> AvFacts:
    accumulators = {"video": _StreamAccumulator(), "audio": _StreamAccumulator()}
    with av.open(str(path)) as container:
        streams = [
            stream for stream in container.streams if stream.type in accumulators
        ]
        for packet in container.demux(streams):
            stream_type = packet.stream.type
            accumulator = accumulators[stream_type]
            accumulator.packets += 1
            for frame in packet.decode():
                _add_frame(accumulator, stream_type, frame, packet.stream)
        for stream in streams:
            for frame in stream.codec_context.decode(None):
                _add_frame(accumulators[stream.type], stream.type, frame, stream)
    return AvFacts(accumulators["video"].facts(), accumulators["audio"].facts())


def _add_frame(
    accumulator: _StreamAccumulator, stream_type: str, frame: object, stream: object
) -> None:
    time_base = getattr(frame, "time_base", None) or getattr(stream, "time_base")
    pts = getattr(frame, "pts", None)
    timestamp = float(pts * time_base) if pts is not None else None
    if timestamp is not None and accumulator.first_seconds is None:
        accumulator.first_seconds = timestamp
    if stream_type == "audio":
        samples = getattr(frame, "samples")
        accumulator.samples += samples
        sample_rate = getattr(frame, "sample_rate")
        if timestamp is not None:
            accumulator.end_seconds = timestamp + samples / sample_rate
    elif timestamp is not None:
        accumulator.end_seconds = timestamp + _video_duration(frame, stream, time_base)
    accumulator.frames += 1


def _video_duration(frame: object, stream: object, time_base: object) -> float:
    duration = getattr(frame, "duration", None)
    if duration is not None:
        return float(duration * time_base)
    average_rate = getattr(stream, "average_rate", None)
    return 1 / float(average_rate) if average_rate else 0


def _report_success(
    console: Console,
    metrics: AcquisitionMetrics | None,
    facts: AvFacts,
    nut_bytes: int,
    buffer_seconds: int,
    seconds: int,
    output_path: Path,
    report: ReportRedaction,
    warnings: list[str],
) -> None:
    table = Table(title="A/V capture experiment")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("NUT artifact", f"{nut_bytes / 1024 / 1024:.1f} MiB")
    table.add_row(
        "Estimated retained buffer",
        f"{nut_bytes * buffer_seconds / seconds / 1024 / 1024:.1f} MiB ({buffer_seconds} s)",
    )
    if metrics:
        table.add_row(
            "Acquisition elapsed / CPU",
            f"{metrics.elapsed_seconds:.2f} s; user {metrics.user_seconds:.2f} s, "
            f"system {metrics.system_seconds:.2f} s; {metrics.cpu_percent:.1f}% of one core",
        )
        table.add_row(
            "Acquisition max RSS", f"{metrics.max_rss_bytes / 1024 / 1024:.1f} MiB"
        )
    else:
        table.add_row("Acquisition metrics", "FFmpeg benchmark data unavailable")
    _add_stream_rows(table, "Video", facts.video)
    _add_stream_rows(table, "Audio", facts.audio)
    if facts.audio.end_seconds is not None and facts.video.end_seconds is not None:
        table.add_row(
            "Audio − video end delta",
            f"{facts.audio.end_seconds - facts.video.end_seconds:+.3f} s",
        )
    if output_path.exists():
        table.add_row(
            "MP4 output",
            f"{report.output_path(output_path)} ({output_path.stat().st_size / 1024 / 1024:.1f} MiB)",
        )
    console.print(table)
    if warnings:
        console.print(
            "[yellow]FFmpeg warnings:[/]\n" + report.ffmpeg_output("\n".join(warnings))
        )
    console.print(
        "Inspect the MP4 and compare the visible and audible beginning/end claps."
    )


def _add_stream_rows(table: Table, name: str, facts: StreamFacts) -> None:
    table.add_row(
        f"{name} first/end PTS",
        f"{_time(facts.first_seconds)} / {_time(facts.end_seconds)}",
    )
    counts = f"{facts.packets} packets, {facts.frames} decoded frames"
    if name == "Audio":
        counts += f", {facts.samples} samples"
    table.add_row(f"{name} decoded", counts)


def _time(value: float | None) -> str:
    return f"{value:.3f} s" if value is not None else "none"


def _report_failure(
    console: Console,
    output: str,
    platform: str,
    device: str,
    phase: str,
    report: ReportRedaction,
) -> None:
    lower = output.lower()
    if (
        platform == "linux"
        and device == "hw:0,0"
        and "device or resource busy" in lower
    ):
        message = "ALSA hw:0,0 is busy. Rerun with --audio-device default."
    elif "timestamp" in lower or "non monoton" in lower:
        message = "FFmpeg timestamp synchronization failed; inspect the warnings below."
    elif "unknown encoder" in lower or "error while opening encoder" in lower:
        message = (
            "FFmpeg encoding failed; the bundled FFmpeg may lack the requested encoder."
        )
    elif (
        "input format" in lower
        or "pixel format" in lower
        or "invalid argument" in lower
    ):
        message = (
            "FFmpeg does not support the requested camera/audio format on this device."
        )
    else:
        message = "FFmpeg could not open a requested device or format. Check camera/audio device availability."
    console.print(f"[bold red]{phase} failed:[/] {message}")
    if report.censor:
        console.print(
            "[dim]FFmpeg warnings:[/] raw device/log output omitted by --censor"
        )
        return
    lines = output.strip().splitlines()
    excerpt = "\n".join(lines[-20:]) or "No FFmpeg diagnostic output."
    console.print(
        f"[dim]FFmpeg warnings (last {min(len(lines), 20)} lines):[/]\n{excerpt}"
    )


def _relevant_warnings(output: str) -> list[str]:
    terms = ("warning", "deprecated", "non-monoton", "timestamp")
    return [
        line
        for line in output.splitlines()
        if any(term in line.lower() for term in terms)
    ][-20:]
