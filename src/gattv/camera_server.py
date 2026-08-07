import asyncio
from pathlib import Path

import aiohttp
from aiohttp import web

from gattv.camera import CameraError, CameraService
from gattv.config import CameraServerConfig
from gattv.motion import MotionService


class CameraServer:
    def __init__(self, config: CameraServerConfig) -> None:
        self.config = config
        self.camera = CameraService(config.camera)
        self.camera_lock = asyncio.Lock()
        self.motion = MotionService(
            self.camera,
            config.motion,
            self.camera_lock,
            self._notify_motion,
            self._send_motion_video,
        )

    def build_application(self) -> web.Application:
        application = web.Application()
        application.add_routes(
            [
                web.get("/status", self.status),
                web.post("/arm", self.arm),
                web.post("/disarm", self.disarm),
                web.post("/photo", self.photo),
                web.post("/video", self.video),
            ]
        )
        return application

    async def status(self, request: web.Request) -> web.Response:
        state = self.motion.state
        return web.json_response(
            {
                "name": self.config.camera.name,
                "armed": state.armed,
                "motion": state.status,
                "last_motion_at": (
                    state.last_motion_at.isoformat()
                    if state.last_motion_at is not None
                    else None
                ),
            }
        )

    async def arm(self, request: web.Request) -> web.Response:
        await self.motion.arm()
        return web.json_response({"armed": True})

    async def disarm(self, request: web.Request) -> web.Response:
        await self.motion.disarm()
        return web.json_response({"armed": False})

    async def photo(self, request: web.Request) -> web.StreamResponse:
        return await self._capture(request, "photo")

    async def video(self, request: web.Request) -> web.StreamResponse:
        return await self._capture(request, "video")

    async def _capture(self, request: web.Request, kind: str) -> web.StreamResponse:
        was_paused = await self.motion.pause()
        if self.camera_lock.locked():
            if was_paused:
                self.motion.resume()
            raise web.HTTPConflict(text="Camera busy, try again in a moment.")

        path: Path | None = None
        await self.camera_lock.acquire()
        try:
            operation = (
                self.camera.capture_photo
                if kind == "photo"
                else self.camera.record_clip
            )
            path = await asyncio.to_thread(operation)
            response = web.FileResponse(path)
            await response.prepare(request)
            await response.write_eof()
            return response
        except CameraError as error:
            raise web.HTTPInternalServerError(text=str(error)) from error
        finally:
            self.camera_lock.release()
            if path is not None:
                path.unlink(missing_ok=True)
            if was_paused:
                self.motion.resume()

    async def _notify_motion(self, text: str) -> None:
        await self._post_motion({"text": text})

    async def _send_motion_video(self, path: Path) -> None:
        with path.open("rb") as video_file:
            data = aiohttp.FormData()
            data.add_field("video", video_file, filename="motion.mp4")
            await self._post_motion(data)

    async def _post_motion(self, data: object) -> None:
        url = f"{self.config.hub_url.rstrip('/')}/motion/{self.config.camera.name}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    if response.status >= 400:
                        print(f"Hub rejected motion event: {await response.text()}")
        except aiohttp.ClientError as error:
            print(f"Could not send motion event to hub: {error}")
