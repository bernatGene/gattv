from collections import deque
from dataclasses import dataclass
import math

from gattv.capture.models import CapturedUnit, CompletedClip, TimelineResult


class TimestampGate:
    def __init__(self, fps: int) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self._interval = 1 / fps
        self._next_at: float | None = None
        self._last_at: float | None = None

    def accepts(self, captured_at: float) -> bool:
        if self._last_at is not None and captured_at < self._last_at:
            raise ValueError("capture timestamps must be monotonic")
        self._last_at = captured_at

        if self._next_at is None:
            self._next_at = captured_at + self._interval
            return True
        if captured_at < self._next_at:
            return False

        elapsed_intervals = math.floor((captured_at - self._next_at) / self._interval)
        self._next_at += (elapsed_intervals + 1) * self._interval
        return True


class RollingBuffer:
    def __init__(self, retention_seconds: float) -> None:
        if retention_seconds < 0:
            raise ValueError("retention_seconds cannot be negative")
        self._retention_seconds = retention_seconds
        self._units: deque[CapturedUnit] = deque()

    def append(self, unit: CapturedUnit, retain_from: float | None = None) -> None:
        if self._units:
            previous = self._units[-1]
            if unit.sequence <= previous.sequence:
                raise ValueError("capture sequence numbers must increase")
            if unit.captured_at < previous.captured_at:
                raise ValueError("capture timestamps must be monotonic")

        self._units.append(unit)
        cutoff = unit.captured_at - self._retention_seconds
        if retain_from is not None:
            cutoff = min(cutoff, retain_from)
        while self._units and self._units[0].captured_at < cutoff:
            self._units.popleft()

    def snapshot(self, started_at: float, ended_at: float) -> tuple[CapturedUnit, ...]:
        if ended_at < started_at:
            raise ValueError("snapshot end cannot precede its start")
        return tuple(
            unit for unit in self._units if started_at <= unit.captured_at <= ended_at
        )

    def __len__(self) -> int:
        return len(self._units)


@dataclass(frozen=True)
class ActiveEvent:
    trigger_at: float
    started_at: float
    deadline: float


class CaptureTimeline:
    def __init__(
        self,
        recording_fps: int,
        detection_fps: int,
        pre_seconds: float,
        post_seconds: float,
        jitter_margin: float = 1.0,
    ) -> None:
        if pre_seconds < 0 or post_seconds < 0:
            raise ValueError("event windows cannot be negative")
        if jitter_margin < 0:
            raise ValueError("jitter_margin cannot be negative")

        self._recording_gate = TimestampGate(recording_fps)
        self._detection_gate = TimestampGate(detection_fps)
        self._buffer = RollingBuffer(pre_seconds + post_seconds + jitter_margin)
        self._pre_seconds = pre_seconds
        self._post_seconds = post_seconds
        self._active_event: ActiveEvent | None = None

    @property
    def event_active(self) -> bool:
        return self._active_event is not None

    @property
    def retained_count(self) -> int:
        return len(self._buffer)

    def ingest(self, unit: CapturedUnit) -> TimelineResult:
        retained = self._recording_gate.accepts(unit.captured_at)
        detect = self._detection_gate.accepts(unit.captured_at)
        if retained:
            retain_from = (
                self._active_event.started_at
                if self._active_event is not None
                else None
            )
            self._buffer.append(unit, retain_from=retain_from)

        completed_clip = None
        event = self._active_event
        if event is not None and unit.captured_at >= event.deadline:
            completed_clip = CompletedClip(
                trigger_at=event.trigger_at,
                started_at=event.started_at,
                ended_at=event.deadline,
                units=self._buffer.snapshot(event.started_at, event.deadline),
            )
            self._active_event = None

        return TimelineResult(
            retained=retained,
            detect=detect,
            completed_clip=completed_clip,
        )

    def trigger(self, trigger_at: float) -> bool:
        if self._active_event is not None:
            return False
        self._active_event = ActiveEvent(
            trigger_at=trigger_at,
            started_at=trigger_at - self._pre_seconds,
            deadline=trigger_at + self._post_seconds,
        )
        return True
