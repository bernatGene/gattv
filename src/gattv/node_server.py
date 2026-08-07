import hmac
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import NamedTemporaryFile

from aiohttp import ClientError, ClientSession, FormData, web

from gattv.camera import CameraError
from gattv.nodes import LocalCameraNode


MotionEventHandler = Callable[[str, Path | None], Awaitable[None]]
NODE_KEY = web.AppKey("node", LocalCameraNode)
MOTION_HANDLER_KEY = web.AppKey("on_motion", MotionEventHandler)


def create_node_app(node: LocalCameraNode, token: str) -> web.Application:
    @web.middleware
    async def authorize(request: web.Request, handler):
        expected = f"Bearer {token}"
        if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
            raise web.HTTPUnauthorized(text="Unauthorized")
        return await handler(request)

    app = web.Application(middlewares=[authorize])
    app[NODE_KEY] = node
    app.router.add_get("/health", _health)
    app.router.add_get("/status", _status)
    app.router.add_post("/arm", _arm)
    app.router.add_post("/disarm", _disarm)
    app.router.add_post("/photo", _photo)
    app.router.add_post("/video", _video)

    return app


def create_hub_app(token: str, on_motion: MotionEventHandler) -> web.Application:
    @web.middleware
    async def authorize(request: web.Request, handler):
        expected = f"Bearer {token}"
        if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
            raise web.HTTPUnauthorized(text="Unauthorized")
        return await handler(request)

    app = web.Application(middlewares=[authorize])
    app[MOTION_HANDLER_KEY] = on_motion
    app.router.add_get("/health", _health)
    app.router.add_post("/events/motion", _motion_event)
    return app


class HubMotionReporter:
    def __init__(
        self, node_name: str, hub_url: str, token: str, session: ClientSession
    ) -> None:
        self.node_name = node_name
        self.hub_url = hub_url.rstrip("/")
        self.token = token
        self.session = session

    async def notify(self, text: str) -> None:
        await self._send(None)

    async def send_video(self, path: Path) -> None:
        await self._send(path)

    async def _send(self, path: Path | None) -> None:
        form = FormData()
        form.add_field("camera", self.node_name)
        file = None
        try:
            if path is not None:
                file = path.open("rb")
                form.add_field(
                    "video",
                    file,
                    filename="gattv-motion.mp4",
                    content_type="video/mp4",
                )
            async with self.session.post(
                self.hub_url + "/events/motion",
                headers={"Authorization": f"Bearer {self.token}"},
                data=form,
            ) as response:
                if response.status != 200:
                    detail = await response.text()
                    raise CameraError(
                        f"Could not report motion to hub: HTTP {response.status}: {detail}"
                    )
        except ClientError as error:
            raise CameraError(f"Could not report motion to hub: {error}") from error
        finally:
            if file is not None:
                file.close()


async def start_web_app(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _status(request: web.Request) -> web.Response:
    node = request.app[NODE_KEY]
    status = await node.status()
    return web.json_response(
        {
            "name": node.name,
            "armed": status.armed,
            "motion_status": status.motion_status,
            "last_motion_at": (
                status.last_motion_at.isoformat() if status.last_motion_at else None
            ),
        }
    )


async def _arm(request: web.Request) -> web.Response:
    node = request.app[NODE_KEY]
    return web.json_response({"changed": await node.arm()})


async def _disarm(request: web.Request) -> web.Response:
    node = request.app[NODE_KEY]
    return web.json_response({"changed": await node.disarm()})


async def _photo(request: web.Request) -> web.StreamResponse:
    node = request.app[NODE_KEY]
    return await _media_response(request, node.capture_photo, "image/jpeg")


async def _video(request: web.Request) -> web.StreamResponse:
    node = request.app[NODE_KEY]
    return await _media_response(request, node.record_clip, "video/mp4")


async def _media_response(
    request: web.Request, capture, content_type: str
) -> web.StreamResponse:
    try:
        path = await capture()
    except CameraError as error:
        raise web.HTTPConflict(text=str(error)) from error
    response = web.StreamResponse(headers={"Content-Type": content_type})
    await response.prepare(request)
    try:
        with path.open("rb") as file:
            while chunk := file.read(64 * 1024):
                await response.write(chunk)
        await response.write_eof()
        return response
    finally:
        path.unlink(missing_ok=True)


async def _motion_event(request: web.Request) -> web.Response:
    reader = await request.multipart()
    camera = ""
    video_path: Path | None = None
    try:
        while field := await reader.next():
            if field.name == "camera":
                camera = await field.text()
            elif field.name == "video":
                with NamedTemporaryFile(
                    prefix="gattv-motion-event-", suffix=".mp4", delete=False
                ) as file:
                    video_path = Path(file.name)
                    while chunk := await field.read_chunk():
                        file.write(chunk)
        if not camera:
            raise web.HTTPBadRequest(text="Missing camera name")
        on_motion = request.app[MOTION_HANDLER_KEY]
        await on_motion(camera, video_path)
    finally:
        if video_path is not None:
            video_path.unlink(missing_ok=True)
    return web.json_response({"accepted": True})
