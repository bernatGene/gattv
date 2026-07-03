# AGENTS.md - gattv Development Guidelines

gattv is a small Python service for monitoring a cat with an old laptop webcam. The
initial interface is a Telegram bot; camera and motion detection code should stay
separate from bot/CLI code so the interface can be changed later.

## Technology Stack

- Python >=3.12,<3.14
- uv for dependency/project management
- Typer for CLI
- Rich for terminal UX
- Pydantic for config validation
- python-telegram-bot for Telegram polling
- OpenCV for camera/motion work when added

## Dev Workflow

- Use `uv` for Python commands and dependency changes.
- Prefer small, direct changes.
- Keep command functions thin; put domain logic outside `cli.py`.
- Keep bot, camera, motion detection, and config concerns separated.
- Do not run Ruff checks/formatters unless explicitly asked.
- Run focused verification after meaningful units of work, not after every edit.
- Happy path only: if domain assumptions break, pause and discuss instead of adding
  workaround complexity.

## Configuration

- Runtime config lives in `gattv.toml` and is gitignored.
- `gattv.example.toml` is the tracked template.
- Config schema is defined in `src/gattv/config.py` using Pydantic.
- Do not add config keys without updating both the schema and example TOML.

## CLI

- The CLI entrypoint is `gattv`.
- Use Typer commands and Rich output.
- The server command is `uv run gattv server`.
- Prefer explicit commands over hidden side effects.

## Code Style

- Keep it simple.
- Use minimal comments; add them only when they explain why.
- Prefer short-circuit logic over nested conditionals.
- Strict type hints.
- Use `list`, `dict`, and other built-in generics rather than `typing.List`.
- Top-level imports only unless there is a concrete reason.

## Git Workflow

- Always work on the current branch.
- Never switch branches unless explicitly requested.
- Only commit if explicitly asked to.
