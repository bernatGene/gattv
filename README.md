# gattv

A small Python service for monitoring a cat with an old laptop webcam.

Run one Telegram bot on a hub laptop and control local or remote laptop webcams.
Each camera detects motion locally, so video crosses the network only for photos,
requested clips, and motion clips.

## Setup

Use Python 3.10. Newer Python versions may force OpenCV to build from source on
older Intel Macs.

```bash
uv python install 3.10
```

Install dependencies:

```bash
uv sync --python 3.10
```

OpenCV is pinned to `4.5.4.60` because newer wheels do not support Monterey
Intel Macs.

Create local config:

```bash
cp gattv.example.toml gattv.toml
```

Edit `gattv.toml` with your Telegram bot token, allowed Telegram user ID, hub
address, shared token, and camera list. A camera without a `url` is attached to
the hub; a camera with a `url` is a remote node.

Run the server:

```bash
uv run gattv server
```

For each remote laptop, copy and edit its node template:

```bash
cp gattv.node.example.toml gattv.toml
uv run gattv node
```

Set `node.hub_url` to the hub laptop's LAN address and use exactly the same
`shared_token` on the hub and every node. Set each remote camera's hub-side URL
to that laptop's LAN address. The HTTP services are intended for a trusted home
LAN and require a bearer token, but do not provide TLS.

On macOS, the server keeps the laptop awake while it is running by starting the
built-in `caffeinate` command. It stops sleep prevention when the server exits.

Test motion detection locally without starting Telegram:

```bash
uv run gattv motion-test
```

## Bot Commands

- `/start` checks that the bot is running.
- `/status` shows whether monitoring is armed, current motion state, and your notification setting.
- `/cameras` selects a camera and presents photo, video, arm, and disarm buttons.
- `/arm` starts motion detection on all cameras.
- `/disarm` stops motion detection on all cameras and releases them.
- `/notify_on` enables motion notifications for your chat.
- `/notify_off` disables motion notifications for your chat.
- `/photo` captures from the selected camera.
- `/video` records from the selected camera.

Only user IDs listed in `gattv.toml` are allowed to control the bot.
Motion notifications are sent to allowed chats that have interacted with the bot
and explicitly enabled notifications with `/notify_on`. Notification settings
are in-memory, default to off, and reset when the server restarts.

The server terminal shows a live status panel with motion state, enabled notify
chats, current task, last Telegram message time, and last motion time.

Camera selection is stored in memory per chat and resets to
`hub.default_camera` when the server restarts. Motion notifications include the
camera name. If a node is unavailable, `/status` reports it as offline while the
other cameras continue working.

On macOS, the terminal app running `gattv` may need camera permission in System
Settings.

If `/photo` is too dark, increase `camera.warmup_frames` in `gattv.toml` so the
webcam has more frames to settle auto-exposure before the photo is sent.

`/video` records a temporary MJPG AVI clip, re-encodes it to H.264 MP4 with the
bundled ffmpeg binary, sends it inline in Telegram, then deletes the temp files.

`motion-test` opens the camera and prints live motion detection state until
`Ctrl+C`. Tune the `[motion]` values in `gattv.toml` until cat-sized movement is
detected without too much noise.

By default, motion sends text notifications only:

```toml
[motion]
mode = "notify"
```

To send motion-triggered MP4 clips instead, set:

```toml
[motion]
mode = "clip"
```

Clip mode uses `motion.pre_seconds` seconds before detection and
`motion.post_seconds` seconds after detection, then sends the encoded MP4 to
chats that have enabled notifications with `/notify_on`.
