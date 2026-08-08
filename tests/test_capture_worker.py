from collections.abc import Iterator
from threading import Event, Thread

from gattv.capture import CapturedUnit, CaptureTimeline, CaptureWorker, CompletedClip


class FakeSource:
    def __init__(self, units: list[CapturedUnit]) -> None:
        self._units = units
        self.closed = False
        self.consumed: list[int] = []

    def units(self) -> Iterator[CapturedUnit]:
        for unit in self._units:
            self.consumed.append(unit.sequence)
            yield unit

    def close(self) -> None:
        self.closed = True


def _unit(sequence: int) -> CapturedUnit:
    return CapturedUnit(
        sequence=sequence,
        captured_at=sequence * 0.25,
        payload=bytes([sequence]),
        codec="mjpeg",
        pixel_format="yuvj422p",
        width=1,
        height=1,
    )


def test_worker_keeps_consuming_while_completed_clip_waits() -> None:
    source = FakeSource([_unit(index) for index in range(13)])
    timeline = CaptureTimeline(2, 1, pre_seconds=1, post_seconds=1)
    triggers_enabled = Event()
    triggers_enabled.set()
    clips: list[CompletedClip] = []

    worker = CaptureWorker(
        source=source,
        timeline=timeline,
        stop_requested=Event(),
        triggers_enabled=triggers_enabled,
        detect_motion=lambda unit: unit.sequence == 4,
        emit_trigger=lambda captured_at: None,
        emit_clip=clips.append,
    )

    worker.run()

    assert source.consumed == list(range(13))
    assert source.closed
    assert len(clips) == 1
    assert [unit.sequence for unit in clips[0].units] == [0, 2, 4, 6, 8]
    assert not triggers_enabled.is_set()


def test_worker_uses_one_sampling_schedule_across_trigger() -> None:
    source = FakeSource([_unit(index) for index in range(13)])
    timeline = CaptureTimeline(2, 1, pre_seconds=0.5, post_seconds=0.5)
    triggers_enabled = Event()
    triggers_enabled.set()
    retained_sequences: list[int] = []
    original_ingest = timeline.ingest

    def ingest(unit: CapturedUnit):
        result = original_ingest(unit)
        if result.retained:
            retained_sequences.append(unit.sequence)
        return result

    timeline.ingest = ingest
    worker = CaptureWorker(
        source,
        timeline,
        Event(),
        triggers_enabled,
        lambda unit: unit.sequence == 4,
        lambda captured_at: None,
        lambda clip: None,
    )

    worker.run()

    assert retained_sequences == [0, 2, 4, 6, 8, 10, 12]


def test_worker_continues_motion_analysis_while_triggers_are_disabled() -> None:
    source = FakeSource([_unit(index) for index in range(9)])
    timeline = CaptureTimeline(4, 1, pre_seconds=0, post_seconds=2)
    triggers_enabled = Event()
    triggers_enabled.set()
    analyzed: list[int] = []

    def detect(unit: CapturedUnit) -> bool:
        analyzed.append(unit.sequence)
        return True

    CaptureWorker(
        source,
        timeline,
        Event(),
        triggers_enabled,
        detect,
        lambda captured_at: None,
        lambda clip: None,
    ).run()

    assert analyzed == [0, 4, 8]


def test_worker_stop_closes_source_to_interrupt_blocked_capture() -> None:
    started = Event()
    released = Event()

    class BlockingSource:
        def units(self) -> Iterator[CapturedUnit]:
            started.set()
            if released.wait():
                return
            yield _unit(0)

        def close(self) -> None:
            released.set()

    worker = CaptureWorker(
        BlockingSource(),
        CaptureTimeline(15, 5, pre_seconds=1, post_seconds=1),
        Event(),
        Event(),
        lambda unit: False,
        lambda captured_at: None,
        lambda clip: None,
    )
    thread = Thread(target=worker.run)
    thread.start()
    assert started.wait(timeout=1)

    worker.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()
