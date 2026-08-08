from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class CapturedUnit:
    sequence: int
    captured_at: float
    payload: bytes
    codec: str
    pixel_format: str
    width: int
    height: int
    source_pts: int | None = None
    source_time_base: Fraction | None = None


@dataclass(frozen=True)
class CompletedClip:
    trigger_at: float
    started_at: float
    ended_at: float
    units: tuple[CapturedUnit, ...]


@dataclass(frozen=True)
class TimelineResult:
    retained: bool
    detect: bool
    completed_clip: CompletedClip | None = None
