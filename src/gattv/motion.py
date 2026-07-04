import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2
import numpy as np

from gattv.camera import CameraError, CameraService
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
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = asyncio.Event()

    async def arm(self) -> bool:
        if self.state.armed:
            return False

        self.state.armed = True
        self._start_task()
        return True

    async def disarm(self) -> bool:
        if not self.state.armed:
            return False

        self.state.armed = False
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
        self.state.status = "watching"
        self._task = asyncio.create_task(self._run())

    async def _stop_task(self, status: str) -> None:
        if self._task is None:
            self.state.status = status
            return

        self._stop_requested.set()
        try:
            await self._task
        except Exception as error:
            print(f"Motion task stopped after error: {error}")
        self._task = None
        self.state.status = status

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
            self.state.armed = False
            self.state.status = "stopped"
            await self.notify(f"Motion detection stopped: {error}")
        except Exception as error:
            self.state.armed = False
            self.state.status = "stopped"
            print(f"Motion detection stopped unexpectedly: {error}")
        finally:
            if samples is not None:
                samples.close()

    async def _run_notify_mode(self, samples: Iterator[MotionSample]) -> None:
        while not self._stop_requested.is_set():
            self.state.status = "watching"
            sample = await asyncio.to_thread(next, samples)
            if sample.detected:
                self.state.last_motion_at = datetime.now()
                self.state.status = "cooldown"
                await self.notify("Motion detected.")
                await self._wait_for_cooldown_or_stop()

    async def _run_clip_mode(self) -> None:
        clips = motion_clips(
            self.camera,
            self.config,
            self._stop_requested.is_set,
            self._set_status,
        )
        try:
            while not self._stop_requested.is_set():
                self.state.status = "watching"
                path = await asyncio.to_thread(_next_clip, clips)
                if path is None:
                    return
                if self._stop_requested.is_set():
                    path.unlink(missing_ok=True)
                    return

                self.state.last_motion_at = datetime.now()
                self.state.status = "sending"
                try:
                    await self.send_video(path)
                finally:
                    path.unlink(missing_ok=True)

                self.state.status = "cooldown"
                await self._wait_for_cooldown_or_stop()
        finally:
            clips.close()

    async def _wait_for_cooldown_or_stop(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop_requested.wait(), timeout=self.config.cooldown_seconds
            )
        except asyncio.TimeoutError:
            pass

    def _set_status(self, status: str) -> None:
        self.state.status = status


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


def motion_clips(
    camera: CameraService,
    config: MotionConfig,
    stop_requested: Callable[[], bool],
    set_status: Callable[[str], None] | None = None,
) -> Iterator[Path]:
    capture = camera._open_capture()
    raw_path = None
    output_path = None
    try:
        frame = camera._warm_up(capture)
        previous = _prepare_frame(frame, config.resize_width)
        prebuffer: deque[np.ndarray] = deque(
            maxlen=max(1, camera.config.fps * config.pre_seconds)
        )
        prebuffer.append(frame.copy())
        consecutive_frames = 0
        frame_interval = 1 / camera.config.fps
        detection_interval = 1 / config.detection_fps
        next_frame_at = time.monotonic()
        next_detection_at = next_frame_at

        while not stop_requested():
            ok, frame = capture.read()
            if not ok:
                raise CameraError("Could not read a frame from the camera.")

            prebuffer.append(frame.copy())

            now = time.monotonic()
            if now >= next_detection_at:
                current = _prepare_frame(frame, config.resize_width)
                sample = _motion_sample(previous, current, consecutive_frames, config)
                previous = current
                consecutive_frames = sample.consecutive_frames
                next_detection_at = now + detection_interval

                if sample.detected:
                    if set_status is not None:
                        set_status("recording")
                    raw_path, output_path = _record_motion_clip(
                        capture, camera, config, list(prebuffer), stop_requested
                    )
                    if stop_requested():
                        raw_path.unlink(missing_ok=True)
                        output_path.unlink(missing_ok=True)
                        return
                    if set_status is not None:
                        set_status("encoding")
                    camera._encode_mp4(raw_path, output_path)
                    raw_path.unlink(missing_ok=True)
                    raw_path = None
                    clip_path = output_path
                    output_path = None
                    yield clip_path
                    prebuffer.clear()
                    consecutive_frames = 0
                    ok, frame = capture.read()
                    if not ok:
                        raise CameraError("Could not read a frame from the camera.")
                    previous = _prepare_frame(frame, config.resize_width)
                    prebuffer.append(frame.copy())
                    next_frame_at = time.monotonic()
                    next_detection_at = next_frame_at + detection_interval

            next_frame_at += frame_interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        capture.release()
        if raw_path is not None:
            raw_path.unlink(missing_ok=True)
        if output_path is not None:
            output_path.unlink(missing_ok=True)


def _next_clip(clips: Iterator[Path]) -> Path | None:
    try:
        return next(clips)
    except StopIteration:
        return None


def _record_motion_clip(
    capture,
    camera: CameraService,
    config: MotionConfig,
    prebuffer: list[np.ndarray],
    stop_requested: Callable[[], bool],
) -> tuple[Path, Path]:
    if not prebuffer:
        raise CameraError("Could not record motion clip without buffered frames.")

    height, width = prebuffer[0].shape[:2]
    with NamedTemporaryFile(
        prefix="gattv-motion-raw-", suffix=".avi", delete=False
    ) as file:
        raw_path = Path(file.name)
    with NamedTemporaryFile(
        prefix="gattv-motion-", suffix=".mp4", delete=False
    ) as file:
        output_path = Path(file.name)

    writer = None
    try:
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(
            str(raw_path), fourcc, camera.config.fps, (width, height)
        )
        if not writer.isOpened():
            raise CameraError("Could not open video writer.")

        for frame in prebuffer:
            writer.write(frame)

        frame_interval = 1 / camera.config.fps
        next_frame_at = time.monotonic()
        end_at = next_frame_at + config.post_seconds
        while time.monotonic() < end_at and not stop_requested():
            ok, frame = capture.read()
            if not ok:
                raise CameraError("Could not read a frame from the camera.")
            writer.write(frame)
            next_frame_at += frame_interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

        writer.release()
        writer = None
        return raw_path, output_path
    except Exception:
        raw_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.release()


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
    height, width = frame.shape[:2]
    resized_height = int(height * (resize_width / width))
    resized = cv2.resize(frame, (resize_width, resized_height))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (21, 21), 0)
