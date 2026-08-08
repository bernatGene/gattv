import pytest

from gattv.capture import CapturedUnit, CaptureTimeline, TimestampGate


def _unit(sequence: int, captured_at: float) -> CapturedUnit:
    return CapturedUnit(
        sequence=sequence,
        captured_at=captured_at,
        payload=f"frame-{sequence}".encode(),
        codec="mjpeg",
        pixel_format="yuvj422p",
        width=640,
        height=480,
    )


def test_timestamp_gate_keeps_stable_cadence_with_irregular_input() -> None:
    gate = TimestampGate(2)
    timestamps = [0.0, 0.2, 0.51, 0.7, 1.02, 1.6]

    selected = [value for value in timestamps if gate.accepts(value)]

    assert selected == [0.0, 0.51, 1.02, 1.6]


def test_timestamp_gate_rejects_non_monotonic_input() -> None:
    gate = TimestampGate(15)
    gate.accepts(1.0)

    with pytest.raises(ValueError, match="monotonic"):
        gate.accepts(0.9)


def test_recording_and_detection_gates_are_independent() -> None:
    timeline = CaptureTimeline(
        recording_fps=2,
        detection_fps=1,
        pre_seconds=1,
        post_seconds=1,
    )

    results = [timeline.ingest(_unit(index, index * 0.25)) for index in range(9)]

    assert [index for index, result in enumerate(results) if result.retained] == [
        0,
        2,
        4,
        6,
        8,
    ]
    assert [index for index, result in enumerate(results) if result.detect] == [0, 4, 8]


def test_completed_clip_uses_one_continuous_timestamp_window() -> None:
    timeline = CaptureTimeline(
        recording_fps=2,
        detection_fps=1,
        pre_seconds=1,
        post_seconds=1,
    )
    retained_units: dict[int, CapturedUnit] = {}
    completed = None

    for index in range(13):
        unit = _unit(index, index * 0.25)
        result = timeline.ingest(unit)
        if result.retained:
            retained_units[index] = unit
        if index == 6:
            assert timeline.trigger(unit.captured_at)
        if result.completed_clip is not None:
            completed = result.completed_clip

    assert completed is not None
    assert completed.trigger_at == 1.5
    assert completed.started_at == 0.5
    assert completed.ended_at == 2.5
    assert [unit.sequence for unit in completed.units] == [2, 4, 6, 8, 10]
    assert completed.units[0] is retained_units[2]
    assert completed.units[-1] is retained_units[10]


def test_trigger_ignores_motion_while_event_is_active() -> None:
    timeline = CaptureTimeline(15, 5, pre_seconds=5, post_seconds=5)

    assert timeline.trigger(10.0)
    assert not timeline.trigger(11.0)


def test_startup_trigger_returns_available_partial_history() -> None:
    timeline = CaptureTimeline(2, 1, pre_seconds=5, post_seconds=1)
    timeline.ingest(_unit(0, 10.0))
    assert timeline.trigger(10.0)

    completed = None
    for index, captured_at in enumerate([10.5, 11.0], start=1):
        result = timeline.ingest(_unit(index, captured_at))
        completed = result.completed_clip or completed

    assert completed is not None
    assert [unit.sequence for unit in completed.units] == [0, 1, 2]


def test_delayed_deadline_packet_does_not_evict_active_clip_history() -> None:
    timeline = CaptureTimeline(
        recording_fps=2,
        detection_fps=1,
        pre_seconds=1,
        post_seconds=1,
        jitter_margin=0,
    )
    for sequence, captured_at in enumerate([0.0, 0.5, 1.0]):
        timeline.ingest(_unit(sequence, captured_at))
    assert timeline.trigger(1.0)

    result = timeline.ingest(_unit(3, 3.0))

    assert result.completed_clip is not None
    assert [unit.sequence for unit in result.completed_clip.units] == [0, 1, 2]
