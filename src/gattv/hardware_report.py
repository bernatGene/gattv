import re
from dataclasses import dataclass
from pathlib import Path


VERSION_PATTERN = re.compile(r"\bversion\s+([0-9][0-9A-Za-z.+-]*)", re.IGNORECASE)


@dataclass(frozen=True)
class ReportRedaction:
    censor: bool = False

    def camera_name(self, value: str) -> str:
        return "<redacted>" if self.censor else value

    def camera_device(self, index: int, platform: str) -> str:
        if not self.censor:
            return ""
        return f"/dev/video{index}" if platform == "linux" else f"video device {index}"

    def detail(self, value: str, failed: bool) -> str:
        if not self.censor:
            return value
        return "probe failed" if failed else "supported"

    def executable(self, value: str) -> str:
        return "<redacted>" if self.censor else value

    def version(self, value: str) -> str:
        if not self.censor:
            return value
        match = VERSION_PATTERN.search(value)
        return f"version {match.group(1)}" if match else "<redacted>"

    def availability_error(self, value: str) -> str:
        return "<redacted>" if self.censor else value

    def listing_message(self) -> str:
        return "raw device listing omitted by --censor"

    def output_path(self, value: Path) -> str:
        return f"<output>/{value.name}" if self.censor else str(value)

    def ffmpeg_output(self, value: str) -> str:
        return self.listing_message() if self.censor else value
