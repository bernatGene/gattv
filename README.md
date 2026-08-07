# gattv

A small Python service for monitoring a cat with an old laptop webcam.

Current scope: run a Telegram bot that can be armed/disarmed and can send an
on-demand camera photo or short video clip.

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

Edit `gattv.toml` with your Telegram bot token, allowed Telegram user ID, and
camera settings.

Run the server:

```bash
uv run gattv server
```

On macOS, the server keeps the laptop awake while it is running by starting the
built-in `caffeinate` command. It stops sleep prevention when the server exits.

Test motion detection locally without starting Telegram:

```bash
uv run gattv motion-test
```

## Bot Commands

- `/start` checks that the bot is running.
- `/status` shows whether monitoring is armed, current motion state, and your notification setting.
- `/arm` starts motion detection.
- `/disarm` stops motion detection and releases the camera.
- `/notify_on` enables motion notifications for your chat.
- `/notify_off` disables motion notifications for your chat.
- `/photo` captures and sends one camera photo.
- `/video` records and sends one short MP4 clip.

Only user IDs listed in `gattv.toml` are allowed to control the bot.
Motion notifications are sent to allowed chats that have interacted with the bot
and explicitly enabled notifications with `/notify_on`. Notification settings
are in-memory, default to off, and reset when the server restarts.

The server terminal shows a live status panel with motion state, enabled notify
chats, current task, last Telegram message time, and last motion time.

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
