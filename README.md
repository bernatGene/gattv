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

## Bot Commands

- `/start` checks that the bot is running.
- `/status` shows whether monitoring is armed.
- `/arm` enables armed state.
- `/disarm` disables armed state.
- `/photo` captures and sends one camera photo.
- `/video` records and sends one short AVI clip.

Only user IDs listed in `gattv.toml` are allowed to control the bot.

On macOS, the terminal app running `gattv` may need camera permission in System
Settings.

If `/photo` is too dark, increase `camera.warmup_frames` in `gattv.toml` so the
webcam has more frames to settle auto-exposure before the photo is sent.

`/video` sends an AVI file using MJPG encoding for compatibility with older
OpenCV/macOS setups.
