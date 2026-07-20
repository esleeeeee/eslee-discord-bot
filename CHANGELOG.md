# Changelog

## Unreleased

- Add an authenticated OneKey HTTP API with `/health` and `/api/voice-status`.
- Enable the non-privileged Discord Voice States intent and query cached guild voice states.
- Add Windows `tzdata` and direct `aiohttp` runtime dependencies plus API regression tests.

All notable changes to this project will be documented in this file.

## [0.1.0] - Unreleased

### Added

- Persistent six-hour announcement reminders backed by SQLite.
- Message context menu and Korean slash commands for announcement management.
- Text, image, file, mixed-attachment, and Discord Poll-aware reminders.
- Poll reminder countdown based on the original Poll expiration time.
- Source-message preservation and jump links without attachment or Poll duplication.
- Forbidden-word moderation for new and edited messages.
- Batch registration of up to 500 comma/newline-separated forbidden words.
- Read-only forbidden-word listing available to every server member.
- Temporary channel warnings that mention the user and auto-delete after about five seconds.
- Per-server moderation audit-log channel settings.
- Privacy-minimized moderation violation records.
- PostgreSQL support through SQLAlchemy's asyncpg dialect while retaining SQLite locally.
- Northflank Developer Sandbox deployment instructions and secret alias configuration.
- Explicit global command sync and guild-scoped announcement repository operations.
- Multi-guild data-isolation and optional development-guild regression tests.
- Northflank `sslmode` translation for SQLAlchemy's asyncpg driver.
- Docker, Docker Compose, Ruff, pytest, and GitHub Actions support.
