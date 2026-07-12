# Changelog

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
- DM-first user warnings with a temporary channel fallback.
- Per-server moderation audit-log channel settings.
- Privacy-minimized moderation violation records.
- Docker, Docker Compose, Ruff, pytest, and GitHub Actions support.
