# Motion Detection Plan

## Current State

### Completed

- Telegram bot runs via `uv run gattv server`.
- Authorized users can use `/start`, `/status`, `/arm`, `/disarm`, `/photo`, and `/video`.
- `/photo` captures a warmed-up camera frame and sends it as a Telegram photo.
- `/video` records a temporary MJPG AVI, re-encodes it to H.264 MP4 with bundled ffmpeg, sends it inline, and deletes temp files.
- Project targets Python 3.10 and OpenCV `4.5.4.60` for Monterey Intel Mac compatibility.
- Motion config exists in `src/gattv/config.py`, `gattv.example.toml`, and `README.md`.
- `uv run gattv motion-test` runs local motion detection without starting Telegram.
- On macOS, `uv run gattv server` prevents idle sleep with `caffeinate` while running.
- `/arm` starts notification-only motion detection, `/disarm` stops it, and `/notify_on`/`/notify_off` control opt-in notifications per chat in memory.
- `motion.mode = "clip"` records and sends motion-triggered MP4 clips to opted-in chats.

### In Progress / Next Steps

- Tune motion thresholds on the real camera and verify cat-sized movement triggers reliably.

---

## Objective

Add reliable motion detection that can first be tuned in notification-only mode, then upgraded to send motion-triggered clips with 5 seconds before and 5 seconds after movement.

## Implementation Details

### Motion Configuration

**Current:** `MotionConfig` exists with defaults:

```toml
[motion]
pre_seconds = 5
post_seconds = 5
cooldown_seconds = 60
detection_fps = 5
resize_width = 320
sensitivity = 25
changed_pixels = 150
consecutive_frames = 2
mode = "notify"
```

`mode = "notify"` means motion sends only a text notification and does not record clips. Later `mode = "clip"` will record/send video.

**Files:**

- `src/gattv/config.py`
- `gattv.example.toml`
- `README.md`

**Done:** Added schema, example TOML, and README mention.

### Motion Test CLI

**Current:** `uv run gattv motion-test` runs the camera motion detection loop without starting Telegram and displays live terminal status.

**Next:** Use it to tune the `[motion]` thresholds on the real camera before connecting detection to `/arm`.

**Files:**

- `src/gattv/motion.py`
- `src/gattv/cli.py`

### Server Motion Mode

**Current:** `/arm` starts notification-only motion detection and `/disarm` stops it.

**Done:** Implemented a first motion loop that:

- Opens the camera while armed.
- Samples frames at `motion.detection_fps`.
- Resizes, grayscales, blurs, diffs frames, thresholds differences, and counts changed pixels.
- Triggers after `motion.consecutive_frames` positive detections.
- Sends immediate Telegram text like `Motion detected.` in notify mode.
- Does not record, encode, or send video yet.
- Applies `motion.cooldown_seconds` after a trigger.

**Files:**

- `src/gattv/motion.py`
- `src/gattv/bot.py`
- `src/gattv/cli.py`

### Bot Workflow

**Current:** `MotionService` owns armed/motion state; the bot owns known chats and per-chat notification settings.

**Done:**

- `/arm` starts the motion loop.
- `/disarm` stops the motion loop and releases the camera.
- `/status` reports armed/disarmed plus motion state.
- `/notify_on` and `/notify_off` control motion notifications independently per known chat.
- `/photo` and `/video` pause detection if armed, run manual media capture, then resume detection.

**Next:** If motion is actively recording/sending in the later clip mode, manual media commands should report `Camera busy, try again in a moment.`

**Files:**

- `src/gattv/bot.py`
- `src/gattv/motion.py`

### CLI Runtime Status

**Current:** CLI shows a live Rich status panel while polling.

**Done:** Added a lightweight runtime status display that reports:

- Armed/disarmed.
- Motion state: `stopped`, `watching`, `paused`, `cooldown`, and later `recording`, `encoding`, `sending`.
- Motion mode: `notify` or `clip`.
- Current task, including sending messages, capturing photos, and recording/encoding/sending videos.
- Last motion event time if available.
- Last Telegram message time if available.

Kept this as a refreshed Rich panel, not a full TUI.

**Files:**

- `src/gattv/cli.py`
- possibly `src/gattv/runtime.py` if shared mutable runtime status needs a small home

### Motion Clip Mode

**Current:** `motion.mode = "clip"` can record and send MP4 clips.

**Done:** Added `mode = "clip"`:

- Maintain a 5-second full-frame prebuffer.
- On motion, record a clip.
- Write prebuffer frames plus 5 seconds after detection to temporary AVI.
- Re-encode to H.264 MP4 with bundled ffmpeg.
- Send inline video to opted-in chats.
- Delete temp files.
- Enter cooldown.

**Files:**

- `src/gattv/motion.py`
- `src/gattv/camera.py`
- `src/gattv/bot.py`

## Open Questions

- Real camera testing showed full-frame dancing around 8000 changed pixels and cat-scale finger movement around 100-200, so the default `changed_pixels` threshold is now 150.

## Success Criteria

- [x] `uv run gattv server` shows startup config and live runtime status.
- [x] `uv run gattv motion-test` runs local detection without starting Telegram.
- [x] `/arm` starts notification-only motion detection by default.
- [ ] A cat-sized movement around 1/10th of the frame triggers `Motion detected.`.
- [ ] Cooldown prevents repeated message spam.
- [x] `/photo` and `/video` work while armed by pausing and resuming detection.
- [x] `/disarm` reliably stops detection and releases the camera.
- [x] Clip mode can send 5s pre + 5s post videos inline in Telegram.
