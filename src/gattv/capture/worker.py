from collections.abc import Callable, Iterator
from threading import Event
from typing import Protocol

import numpy as np

from gattv.capture.buffer import CaptureTimeline
from gattv.capture.models import CapturedUnit, CompletedClip


class CaptureSource(Protocol):
    def units(self) -> Iterator[CapturedUnit]: ...

    def detection_image(self, unit: CapturedUnit) -> np.ndarray: ...

    def close(self) -> None: ...


class CaptureWorker:
    def __init__(
        self,
        source: CaptureSource,
        timeline: CaptureTimeline,
        stop_requested: Event,
        triggers_enabled: Event,
        detect_motion: Callable[[CapturedUnit], bool],
        emit_trigger: Callable[[float], None],
        emit_clip: Callable[[CompletedClip], None],
    ) -> None:
        self._source = source
        self._timeline = timeline
        self._stop_requested = stop_requested
        self._triggers_enabled = triggers_enabled
        self._detect_motion = detect_motion
        self._emit_trigger = emit_trigger
        self._emit_clip = emit_clip

    def run(self) -> None:
        try:
            for unit in self._source.units():
                if self._stop_requested.is_set():
                    return

                result = self._timeline.ingest(unit)
                if result.completed_clip is not None:
                    self._emit_clip(result.completed_clip)

                motion_detected = result.detect and self._detect_motion(unit)
                if (
                    motion_detected
                    and self._triggers_enabled.is_set()
                    and not self._timeline.event_active
                    and self._timeline.trigger(unit.captured_at)
                ):
                    self._triggers_enabled.clear()
                    self._emit_trigger(unit.captured_at)
        except Exception:
            if not self._stop_requested.is_set():
                raise
        finally:
            self._source.close()

    def stop(self) -> None:
        self._stop_requested.set()
        self._source.close()
