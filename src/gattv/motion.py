import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from tempfile import NamedTemporaryFile
from threading import Event as ThreadEvent

import cv2
import numpy as np

from gattv.camera import CameraError, CameraService
from gattv.capture import (
    CaptureTimeline,
    CaptureWorker,
    CapturedUnit,
    CompletedClip,
    create_capture_source,
    encode_clip,
)
from gattv.config import MotionConfig


@dataclass(frozen=True)
class MotionSample:
    changed_pixels: int
    consecutive_frames: int
    detected: bool


@dataclass
class MotionState:
    armed: bool = False
    status: str = "stopped"
    last_motion_at: datetime | None = None


class MotionDetector:
    def __init__(self, config: MotionConfig) -> None:
        self._config = config
        self._previous: np.ndarray | None = None
        self._consecutive_frames = 0

    def detect(self, image: np.ndarray) -> bool:
        current = _prepare_gray_frame(image, self._config.resize_width)
        if self._previous is None:
            self._previous = current
            return False

        sample = _motion_sample(
            self._previous,
            current,
            self._consecutive_frames,
            self._config,
        )
        self._previous = current
        self._consecutive_frames = sample.consecutive_frames
        return sample.detected


class MotionService:
    def __init__(
        self,
        camera: CameraService,
        config: MotionConfig,
        camera_lock: asyncio.Lock,
        notify: Callable[[str], Awaitable[None]],
        send_video: Callable[[Path], Awaitable[None]],
    ) -> None:
        self.camera = camera
        self.config = config
        self.camera_lock = camera_lock
        self.notify = notify
        self.send_video = send_video
        self.state = MotionState()
        self.state_changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = asyncio.Event()
        self._capture_stop: ThreadEvent | None = None
        self._capture_worker: CaptureWorker | None = None
        self._notification_task: asyncio.Task[None] | None = None

    async def arm(self) -> bool:
        if self.state.armed:
            return False

        self._update_state(armed=True)
        self._start_task()
        return True

    async def disarm(self) -> bool:
        if not self.state.armed:
            return False

        self._update_state(armed=False)
        await self._stop_task("stopped")
        return True

    async def pause(self) -> bool:
        if self._task is None:
            return False

        await self._stop_task("paused")
        return True

    def resume(self) -> None:
        if self.state.armed and self._task is None:
            self._start_task()

    def _start_task(self) -> None:
        self._stop_requested = asyncio.Event()
        self._update_state(status="watching")
        self._task = asyncio.create_task(self._run())

    async def _stop_task(self, status: str) -> None:
        if self._task is None:
            self._update_state(status=status)
            return

        self._stop_requested.set()
        if self._capture_worker is not None:
            await asyncio.to_thread(self._capture_worker.stop)
        try:
            await self._task
        except Exception as error:
            print(f"Motion task stopped after error: {error}")
        self._task = None
        self._update_state(status=status)

    async def _run(self) -> None:
        samples: Iterator[MotionSample] | None = None
        try:
            async with self.camera_lock:
                if self._stop_requested.is_set():
                    return

                if self.config.mode == "clip":
                    await self._run_clip_mode()
                    return

                samples = motion_samples(self.camera, self.config)
                await self._run_notify_mode(samples)
        except CameraError as error:
            self._update_state(armed=False, status="stopped")
            await self.notify(f"Motion detection stopped: {error}")
        except Exception as error:
            self._update_state(armed=False, status="stopped")
            print(f"Motion detection stopped unexpectedly: {error}")
        finally:
            if samples is not None:
                samples.close()

    async def _run_notify_mode(self, samples: Iterator[MotionSample]) -> None:
        while not self._stop_requested.is_set():
            self._update_state(status="watching")
            sample = await asyncio.to_thread(next, samples)
            if sample.detected:
                self._update_state(last_motion_at=datetime.now(), status="cooldown")
                await self.notify("Motion detected.")
                await self._wait_for_cooldown_or_stop()

    async def _run_clip_mode(self) -> None:
        loop = asyncio.get_running_loop()
        source = create_capture_source(self.camera.config)
        timeline = CaptureTimeline(
            recording_fps=self.camera.config.fps,
            detection_fps=self.config.detection_fps,
            pre_seconds=self.config.pre_seconds,
            post_seconds=self.config.post_seconds,
        )
        detector = MotionDetector(self.config)
        events: Queue[CompletedClip | Exception | None] = Queue(maxsize=1)
        self._capture_stop = ThreadEvent()
        triggers_enabled = ThreadEvent()
        triggers_enabled.set()

        def detect_motion(unit: CapturedUnit) -> bool:
            return detector.detect(source.detection_image(unit))

        def emit_clip(clip: CompletedClip) -> None:
            events.put(clip)

        worker = CaptureWorker(
            source=source,
            timeline=timeline,
            stop_requested=self._capture_stop,
            triggers_enabled=triggers_enabled,
            detect_motion=detect_motion,
            emit_trigger=lambda captured_at: loop.call_soon_threadsafe(
                self._capture_triggered
            ),
            emit_clip=emit_clip,
        )
        self._capture_worker = worker

        def run_worker() -> None:
            try:
                worker.run()
            except Exception as error:
                events.put(error)
            else:
                events.put(None)

        worker_task = asyncio.create_task(asyncio.to_thread(run_worker))
        try:
            while not self._stop_requested.is_set():
                event = await asyncio.to_thread(events.get)
                if event is None:
                    if not self._stop_requested.is_set():
                        raise CameraError("Camera capture stopped unexpectedly.")
                    return
                if isinstance(event, Exception):
                    raise event

                await self._process_clip(event)
                if not self._stop_requested.is_set():
                    triggers_enabled.set()
                    self._update_state(status="watching")
        finally:
            await asyncio.to_thread(self._capture_worker.stop)
            try:
                await worker_task
            finally:
                self._capture_worker = None
                self._capture_stop = None
                if self._notification_task is not None:
                    self._notification_task.cancel()
                    await asyncio.gather(
                        self._notification_task, return_exceptions=True
                    )
                    self._notification_task = None

    def _capture_triggered(self) -> None:
        if self._stop_requested.is_set() or not self.state.armed:
            return
        self._update_state(last_motion_at=datetime.now(), status="recording")
        self._notification_task = asyncio.create_task(self._notify_clip_detection())

    async def _process_clip(self, clip: CompletedClip) -> None:
        with NamedTemporaryFile(
            prefix="gattv-motion-", suffix=".mp4", delete=False
        ) as file:
            path = Path(file.name)
        try:
            self._update_state(status="encoding")
            await asyncio.to_thread(encode_clip, clip, path, self.camera.config.fps)
            if self._notification_task is not None:
                await self._notification_task
                self._notification_task = None
            if self._stop_requested.is_set():
                return
            self._update_state(status="sending")
            await self.send_video(path)
            self._update_state(status="cooldown")
            await self._wait_for_cooldown_or_stop()
        finally:
            path.unlink(missing_ok=True)

    async def _notify_clip_detection(self) -> None:
        await self.notify("Motion detected. Recording video...")

    async def _wait_for_cooldown_or_stop(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop_requested.wait(), timeout=self.config.cooldown_seconds
            )
        except asyncio.TimeoutError:
            pass

    def _update_state(
        self,
        *,
        armed: bool | None = None,
        status: str | None = None,
        last_motion_at: datetime | None = None,
    ) -> None:
        changed = False
        if armed is not None and self.state.armed != armed:
            self.state.armed = armed
            changed = True
        if status is not None and self.state.status != status:
            self.state.status = status
            changed = True
        if last_motion_at is not None and self.state.last_motion_at != last_motion_at:
            self.state.last_motion_at = last_motion_at
            changed = True
        if changed:
            self.state_changed.set()


def motion_samples(
    camera: CameraService, config: MotionConfig
) -> Iterator[MotionSample]:
    capture = camera._open_capture()
    try:
        frame = camera._warm_up(capture)
        previous = _prepare_frame(frame, config.resize_width)
        consecutive_frames = 0
        frame_interval = 1 / config.detection_fps
        next_frame_at = time.monotonic()

        while True:
            ok, frame = capture.read()
            if not ok:
                raise CameraError("Could not read a frame from the camera.")

            current = _prepare_frame(frame, config.resize_width)
            delta = cv2.absdiff(previous, current)
            threshold = cv2.threshold(
                delta, config.sensitivity, 255, cv2.THRESH_BINARY
            )[1]
            changed_pixels = cv2.countNonZero(threshold)

            if changed_pixels >= config.changed_pixels:
                consecutive_frames += 1
            else:
                consecutive_frames = 0

            previous = current
            yield MotionSample(
                changed_pixels=changed_pixels,
                consecutive_frames=consecutive_frames,
                detected=consecutive_frames >= config.consecutive_frames,
            )

            next_frame_at += frame_interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        capture.release()


def _motion_sample(
    previous: np.ndarray,
    current: np.ndarray,
    consecutive_frames: int,
    config: MotionConfig,
) -> MotionSample:
    delta = cv2.absdiff(previous, current)
    threshold = cv2.threshold(delta, config.sensitivity, 255, cv2.THRESH_BINARY)[1]
    changed_pixels = cv2.countNonZero(threshold)

    if changed_pixels >= config.changed_pixels:
        consecutive_frames += 1
    else:
        consecutive_frames = 0

    return MotionSample(
        changed_pixels=changed_pixels,
        consecutive_frames=consecutive_frames,
        detected=consecutive_frames >= config.consecutive_frames,
    )


def _prepare_frame(frame: np.ndarray, resize_width: int) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return _prepare_gray_frame(gray, resize_width)


def _prepare_gray_frame(frame: np.ndarray, resize_width: int) -> np.ndarray:
    height, width = frame.shape
    resized_height = int(height * (resize_width / width))
    resized = cv2.resize(frame, (resize_width, resized_height))
    return cv2.GaussianBlur(resized, (21, 21), 0)
