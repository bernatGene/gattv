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
cp gattv.example.toml gattv.toml
uv run gattv hub
```

Configure the Telegram credentials, default camera, and camera URLs in
`gattv.toml`.

## Cameras

Create a separate config for each camera process:

```bash
cp gattv.camera.example.toml gattv.camera.toml
uv run gattv camera --config-path gattv.camera.toml
```

For a camera on the hub laptop, use `127.0.0.1` for both URLs. For a remote
camera, set `listen_host = "0.0.0.0"`, set `hub_url` to the hub laptop's LAN
address, and add the camera's LAN URL to the hub config. Camera names must match.

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

Notification and selected-camera settings are in memory and reset when the hub
restarts.
