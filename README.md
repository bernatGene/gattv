# gattv

A small Python service for monitoring a cat with an old laptop webcam.

Current scope: run a Telegram bot that can be armed/disarmed and can send an
on-demand camera photo.

## Setup

Use Python 3.12 or 3.13. Python 3.14 may force OpenCV to build from source on
older Macs.

```bash
uv python install 3.12
```

Install dependencies:

```bash
uv sync --python 3.12
```

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

Only user IDs listed in `gattv.toml` are allowed to control the bot.

On macOS, the terminal app running `gattv` may need camera permission in System
Settings.

If `/photo` is too dark, increase `camera.warmup_frames` in `gattv.toml` so the
webcam has more frames to settle auto-exposure before the photo is sent.
