# gattv

A small Python service for monitoring a cat with an old laptop webcam.

Current scope: run a Telegram bot that can be armed/disarmed. Camera and motion
detection will be added next.

## Setup

Install dependencies:

```bash
uv sync
```

Create local config:

```bash
cp gattv.example.toml gattv.toml
```

Edit `gattv.toml` with your Telegram bot token and allowed Telegram user ID.

Run the server:

```bash
uv run gattv server
```

## Bot Commands

- `/start` checks that the bot is running.
- `/status` shows whether monitoring is armed.
- `/arm` enables armed state.
- `/disarm` disables armed state.

Only user IDs listed in `gattv.toml` are allowed to control the bot.
