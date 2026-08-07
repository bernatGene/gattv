from dataclasses import dataclass
import ipaddress
from pathlib import Path
import socket
from threading import Event

import aiohttp
from pydantic import BaseModel, Field
from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from gattv.config import CameraServerConfig, HubServerConfig


SERVICE_TYPE = "_gattv._tcp.local."


class CameraRegistration(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://")


@dataclass(frozen=True)
class DiscoveredHub:
    name: str
    url: str


def local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        try:
            connection.connect(("192.0.2.1", 80))
            address = connection.getsockname()[0]
        except OSError:
            return "127.0.0.1"
    try:
        return address if ipaddress.ip_address(address).is_private else "127.0.0.1"
    except ValueError:
        return "127.0.0.1"


def write_hub_config(path: Path, config: HubServerConfig) -> None:
    user_ids = ", ".join(
        str(user_id) for user_id in sorted(config.telegram.allowed_user_ids)
    )
    lines = [
        "[telegram]",
        f'bot_token = "{_escape(config.telegram.bot_token)}"',
        f"allowed_user_ids = [{user_ids}]",
        "",
        "[hub]",
        f'listen_host = "{_escape(config.hub.listen_host)}"',
        f"listen_port = {config.hub.listen_port}",
    ]
    if config.hub.default_camera is not None:
        lines.append(f'default_camera = "{_escape(config.hub.default_camera)}"')
    lines.extend(["", "[hub.cameras]"])
    lines.extend(
        f'"{_escape(name)}" = "{_escape(url)}"'
        for name, url in sorted(config.hub.cameras.items())
    )
    _atomic_write(path, "\n".join(lines) + "\n")


def write_camera_config(path: Path, config: CameraServerConfig) -> None:
    camera = config.camera
    motion = config.motion
    content = f'''listen_host = "{_escape(config.listen_host)}"
listen_port = {config.listen_port}
hub_url = "{_escape(config.hub_url)}"

[camera]
name = "{_escape(camera.name)}"
index = {camera.index}
width = {camera.width}
height = {camera.height}
fps = {camera.fps}
warmup_frames = {camera.warmup_frames}
clip_seconds = {camera.clip_seconds}

[motion]
pre_seconds = {motion.pre_seconds}
post_seconds = {motion.post_seconds}
cooldown_seconds = {motion.cooldown_seconds}
detection_fps = {motion.detection_fps}
resize_width = {motion.resize_width}
sensitivity = {motion.sensitivity}
changed_pixels = {motion.changed_pixels}
consecutive_frames = {motion.consecutive_frames}
mode = "{motion.mode}"
'''
    _atomic_write(path, content)


def advertise_hub(address: str, port: int) -> tuple[Zeroconf, ServiceInfo]:
    zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
    info = ServiceInfo(
        SERVICE_TYPE,
        f"gattv hub.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(address)],
        port=port,
        properties={"path": "/"},
        server=f"{socket.gethostname()}.local.",
    )
    zeroconf.register_service(info)
    return zeroconf, info


def discover_hubs(timeout: float = 3.0) -> list[DiscoveredHub]:
    listener = _HubListener()
    zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
    browser = ServiceBrowser(zeroconf, SERVICE_TYPE, listener)
    listener.changed.wait(timeout)
    browser.cancel()
    zeroconf.close()
    return sorted(listener.hubs.values(), key=lambda hub: hub.name)


async def register_camera(hub_url: str, registration: CameraRegistration) -> None:
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{hub_url.rstrip('/')}/register", json=registration.model_dump()
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(text or f"Hub returned HTTP {response.status}")


class _HubListener(ServiceListener):
    def __init__(self) -> None:
        self.hubs: dict[str, DiscoveredHub] = {}
        self.changed = Event()

    def add_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        info = zeroconf.get_service_info(type_, name)
        if info is None:
            return
        addresses = info.parsed_scoped_addresses(IPVersion.V4Only)
        if not addresses:
            return
        display_name = name.removesuffix(f".{SERVICE_TYPE}")
        self.hubs[name] = DiscoveredHub(
            display_name, f"http://{addresses[0]}:{info.port}"
        )
        self.changed.set()

    def update_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zeroconf, type_, name)

    def remove_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        self.hubs.pop(name, None)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content)
    temporary.replace(path)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
