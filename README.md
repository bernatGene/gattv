# gattv

A small multi-camera cat monitoring service using old laptop webcams.

The hub runs the Telegram bot. Each camera runs as a separate process and owns
its webcam and motion detection. A camera on the hub laptop works exactly like a
camera on another laptop: the hub reaches both over HTTP.

## Setup

Use Python 3.10, then install dependencies:

```bash
uv python install 3.10
uv sync --python 3.10
```

OpenCV is pinned because newer wheels do not support Monterey Intel Macs.

## Hub

```bash
uv run gattv config init hub
uv run gattv hub
```

The wizard detects the hub's LAN address and asks for the Telegram credentials.
The running hub advertises itself over mDNS so camera setup can find it.

## Cameras

Create a separate config for each camera process:

```bash
uv run gattv config init camera
uv run gattv camera
```

Start the hub before running the camera wizard. It discovers hubs on the local
network, detects the camera laptop's LAN address, writes the config, and asks the
hub to register the camera. Approve the request in the hub terminal. If mDNS is
unavailable, enter the hub URL shown by the running hub.

The default files are `gattv.toml` for the hub and `gattv.camera.toml` for a
camera. Use `--config-path` to create multiple configs. Existing files are never
overwritten without confirmation. The tracked `gattv.example.toml` and
`gattv.camera.example.toml` files document manual configuration.

Run one camera process per laptop. On macOS, the terminal may need camera
permission in System Settings. Both hub and camera commands keep the laptop
awake with `caffeinate` while running.

## Bot Commands

- `/cameras` selects the camera used by `/photo` and `/video`.
- `/photo` captures from the selected camera.
- `/video` records from the selected camera.
- `/status` reports all configured cameras.
- `/arm` and `/disarm` operate on all cameras.
- `/notify_on` and `/notify_off` control motion notifications for the chat.

Motion notifications default to off. Each chat can opt in with `/notify_on` and
pause notifications with `/notify_off`; this choice persists across hub restarts.
Selected-camera settings remain in memory and reset when the hub restarts.
