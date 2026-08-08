import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile

import aiohttp

from gattv.camera import CameraError


REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=5, connect=2)
MEDIA_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=5)


class CameraClient:
    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self.url = url.rstrip("/")

    async def status(self) -> dict[str, object]:
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                async with session.get(f"{self.url}/status") as response:
                    await self._check_response(response)
                    return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise CameraError(f"Could not reach {self.name}: {error}") from error

    async def arm(self) -> None:
        await self._post("arm")

    async def disarm(self) -> None:
        await self._post("disarm")

    async def capture_photo(self) -> Path:
        return await self._download("photo", ".jpg")

    async def record_clip(self) -> Path:
        return await self._download("video", ".mp4")

    async def _post(self, action: str) -> None:
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                async with session.post(f"{self.url}/{action}") as response:
                    await self._check_response(response)
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise CameraError(f"Could not reach {self.name}: {error}") from error

    async def _download(self, action: str, suffix: str) -> Path:
        path: Path | None = None
        try:
            async with aiohttp.ClientSession(timeout=MEDIA_TIMEOUT) as session:
                async with session.post(f"{self.url}/{action}") as response:
                    await self._check_response(response)
                    with NamedTemporaryFile(
                        prefix=f"gattv-{self.name}-", suffix=suffix, delete=False
                    ) as file:
                        path = Path(file.name)
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            file.write(chunk)
            return path
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            if path is not None:
                path.unlink(missing_ok=True)
            raise CameraError(f"Could not reach {self.name}: {error}") from error
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    async def _check_response(self, response: aiohttp.ClientResponse) -> None:
        if response.status < 400:
            return
        message = await response.text()
        raise CameraError(
            message or f"Camera request failed with HTTP {response.status}."
        )
