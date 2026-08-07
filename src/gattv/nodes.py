import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from aiohttp import ClientError, ClientSession

from gattv.camera import CameraError, CameraService
from gattv.config import MotionConfig
from gattv.motion import MotionService


@dataclass(frozen=True)
class CameraNodeStatus:
    armed: bool
    motion_status: str
    last_motion_at: datetime | None


class CameraNode(Protocol):
    name: str
    clip_seconds: int

    async def status(self) -> CameraNodeStatus: ...

    async def arm(self) -> bool: ...

    async def disarm(self) -> bool: ...

    async def capture_photo(self) -> Path: ...

    async def record_clip(self) -> Path: ...

    async def close(self) -> None: ...


class LocalCameraNode:
    def __init__(
        self,
        name: str,
        camera: CameraService,
        motion_config: MotionConfig,
        notify,
        send_video,
    ) -> None:
        self.name = name
        self.camera = camera
        self.clip_seconds = camera.config.clip_seconds
        self.lock = asyncio.Lock()
        self.motion = MotionService(
            camera, motion_config, self.lock, notify, send_video
        )

    async def status(self) -> CameraNodeStatus:
        return CameraNodeStatus(
            armed=self.motion.state.armed,
            motion_status=self.motion.state.status,
            last_motion_at=self.motion.state.last_motion_at,
        )

    async def arm(self) -> bool:
        return await self.motion.arm()

    async def disarm(self) -> bool:
        return await self.motion.disarm()

    async def capture_photo(self) -> Path:
        was_paused = await self.motion.pause()
        if self.lock.locked():
            if was_paused:
                self.motion.resume()
            raise CameraError("Camera busy, try again in a moment.")

        try:
            async with self.lock:
                return await asyncio.to_thread(self.camera.capture_photo)
        finally:
            if was_paused:
                self.motion.resume()

    async def record_clip(self) -> Path:
        was_paused = await self.motion.pause()
        if self.lock.locked():
            if was_paused:
                self.motion.resume()
            raise CameraError("Camera busy, try again in a moment.")

        try:
            async with self.lock:
                return await asyncio.to_thread(self.camera.record_clip)
        finally:
            if was_paused:
                self.motion.resume()

    async def close(self) -> None:
        await self.motion.disarm()


class RemoteCameraNode:
    def __init__(
        self,
        name: str,
        url: str,
        token: str,
        session: ClientSession,
        clip_seconds: int,
    ) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.token = token
        self.session = session
        self.clip_seconds = clip_seconds

    async def status(self) -> CameraNodeStatus:
        data = await self._json("GET", "/status")
        last_motion = data.get("last_motion_at")
        return CameraNodeStatus(
            armed=bool(data["armed"]),
            motion_status=str(data["motion_status"]),
            last_motion_at=datetime.fromisoformat(last_motion) if last_motion else None,
        )

    async def arm(self) -> bool:
        return bool((await self._json("POST", "/arm"))["changed"])

    async def disarm(self) -> bool:
        return bool((await self._json("POST", "/disarm"))["changed"])

    async def capture_photo(self) -> Path:
        return await self._download("/photo", ".jpg")

    async def record_clip(self) -> Path:
        return await self._download("/video", ".mp4")

    async def close(self) -> None:
        return None

    async def _json(self, method: str, path: str) -> dict[str, object]:
        try:
            async with self.session.request(
                method, self.url + path, headers=self._headers()
            ) as response:
                if response.status != 200:
                    raise CameraError(
                        await self._error(response.status, await response.text())
                    )
                data = await response.json()
        except ClientError as error:
            raise CameraError(
                f"Camera node {self.name} is unavailable: {error}"
            ) from error
        if not isinstance(data, dict):
            raise CameraError(f"Camera node {self.name} returned an invalid response.")
        return data

    async def _download(self, endpoint: str, suffix: str) -> Path:
        path: Path | None = None
        try:
            async with self.session.post(
                self.url + endpoint, headers=self._headers()
            ) as response:
                if response.status != 200:
                    raise CameraError(
                        await self._error(response.status, await response.text())
                    )
                with NamedTemporaryFile(
                    prefix="gattv-remote-", suffix=suffix, delete=False
                ) as file:
                    path = Path(file.name)
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        file.write(chunk)
        except ClientError as error:
            if path is not None:
                path.unlink(missing_ok=True)
            raise CameraError(
                f"Camera node {self.name} is unavailable: {error}"
            ) from error
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            raise
        if path is None:
            raise CameraError(f"Camera node {self.name} returned no media.")
        return path

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def _error(self, status: int, text: str) -> str:
        return f"Camera node {self.name} returned HTTP {status}: {text}"
