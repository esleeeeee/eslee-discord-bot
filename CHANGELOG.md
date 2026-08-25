# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

- Add an authenticated OneKey HTTP API with `/health` and `/api/voice-status`.
- Add `POST /api/guild-intersection`, which answers with the subset of the caller's own
  guild ids that the bot also belongs to. The bot never enumerates its guilds, so the
  reply cannot name a server the caller did not already send. Ids must be strings
  (a 19-digit snowflake exceeds 2**53 and would be corrupted as a JSON number), at most
  200 per request, and no names, channels or member data are returned.
- Enable the non-privileged Discord Voice States intent and query cached guild voice states.
- Add Windows `tzdata` and direct `aiohttp` runtime dependencies plus API regression tests.
- Require `ONEKEY_API_TOKEN` to be at least 32 characters and reject whitespace-padded
  tokens at configuration time instead of silently trimming them.
- Return only `{"in_voice": boolean}` from `/api/voice-status`; the guild, channel, and
  channel name are no longer disclosed.
- Send `Cache-Control: no-store` from both endpoints and `Vary: Authorization` from the
  authenticated one so no cache stores or shares a presence response.
- Hide rejected input from settings validation errors so a bad token or database URL
  cannot reach a deployment log.
- Read the voice state through `Guild._voice_state_for`/`Member.voice`. `Guild` has no
  public `voice_states` mapping, so the previous lookup reported every user as absent
  against real Discord objects while mocked tests passed.
- Compare the bearer credential as bytes so a non-ASCII or malformed Authorization
  header returns 401 instead of a 500 with a traceback.

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
- Free-tier aware Gemini budgeting for daily summaries: a pre-flight request estimate,
  a persisted per-report-date call counter shared by automatic, manual, and catch-up
  runs, chunk-level checkpoints that survive restarts, RPM/TPM pacing, and a
  guild-wide daily-quota hold until the Pacific reset.
- Daily summary AI budgets are counted per Pacific quota window, so a report whose
  budget was spent in an earlier window catches up after the next reset instead of
  staying blocked, while its lifetime request total is preserved for auditing.
- `/하루요약 상태` now reports message count, estimated input tokens, planned and
  completed chunks, Gemini calls against the cap, the last failed stage, and the
  cooldown end time.
- Docker, Docker Compose, Ruff, pytest, and GitHub Actions support.
