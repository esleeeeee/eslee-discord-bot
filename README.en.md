# eslee Discord Bot

[➕ **Add eslee-bot to your Discord server**](https://discord.com/oauth2/authorize?client_id=1525689872621240442&scope=bot+applications.commands&permissions=2147576832)

This permanent invite URL points to the official bot application. Servers that install it automatically use the currently deployed bot version; no re-invitation is required after updates.

**Language:** English · [한국어](README.md)

`eslee Discord Bot` is a small-server Discord assistant that keeps important notices visible, removes forbidden words in real time, and can publish a Gemini-powered daily conversation report for one configured channel. Discord pins are easy to miss—especially on mobile—so registered source messages are resurfaced every six hours without destroying Poll results or repeatedly uploading the same images and files.

The bot also provides persistent, server-specific moderation with DM-first warnings, a short-lived channel fallback, and optional administrator audit logs.

## Overview

- Python 3.12 and `discord.py` 2.7.1
- Async SQLAlchemy 2.x with SQLite/`aiosqlite` and PostgreSQL/`asyncpg`
- Database-driven scheduling that survives process restarts
- Korean user-facing commands and responses
- Minimal Discord intents and permissions

## Why This Project

Discord's pin list requires members to look for it manually. This bot periodically exposes important messages in the channel while keeping one canonical source. A Poll reminder links back to the original Poll, preserving every vote. Attachment reminders reference existing Discord-hosted files instead of re-uploading them.

Moderation follows the same practical approach: matching is predictable, warnings are private when possible, and stored violation records exclude the original message body.

## Features

- Multiple concurrent announcements per server
- Immediate first reminder, then a fixed six-hour cadence
- Previous reminder cleanup before the next reminder
- Short text, long text, image, file, mixed, and Poll-aware embeds
- Source edits reflected in later reminders
- Source deletion automatically disables the announcement
- Case-insensitive, Unicode-normalized substring moderation
- Bounded detection of punctuation, digit, whitespace, zero-width, `ㅋ/ㅎ`, and Hangul Jamo evasions
- New-message and uncached raw message-edit inspection
- Multiple matches handled as one deletion, one warning, and one violation
- Other bots and webhooks ignored by default
- Optional daily summary collection, Seoul-midnight startup backfill, and Gemini reports
- Server owner or Discord Administrator management policy

## Announcement Reminder System

Announcements are stored in SQLite with `last_sent_at` and `next_send_at`. A single scheduler polls due rows every 60 seconds by default. It does not reset a six-hour timer on every restart. After downtime, each due announcement is sent at most once, and missed time slots are skipped until the next future six-hour boundary.

Each reminder:

1. Fetches the current source message.
2. Attempts to delete the prior reminder; an already-deleted reminder is harmless.
3. Builds a content-aware embed with a source jump link.
4. Sends the new reminder.
5. Atomically updates the snapshot, reminder ID, sent time, and next due time.

Images and files are referenced, never re-uploaded. Polls are never copied or deleted; their reminders display the real question, known finalized state, and remaining time such as `25시간 30분`, then link to the original Poll. Participation counts are intentionally omitted because a summed vote count is not a reliable unique-participant count for multi-select Polls.

## Forbidden Word Moderation

Matching keeps the existing Unicode NFKC plus `casefold()` substring rule, then performs a bounded evasion check. For a registered word such as `주식`, forms like `주.식`, `주123식`, `주 식`, `주ㅋㅋ식`, zero-width insertion, and separated Jamo are detected without globally deleting separators or using an unrestricted wildcard. Only approved filler categories are allowed, with at most eight characters between meaningful characters, so ordinary Korean text with unrelated words in between is not joined into a match.

When one or more words match, the bot attempts message deletion, warns the user by DM, falls back to a channel warning deleted after about five seconds, posts an optional audit embed, and records only IDs plus the originally registered matched words in the database.

Messages from bots, the bot itself, and webhooks are ignored. Both newly created messages and message edits are inspected. Attachment-only messages have no text and are ignored.

## Optional Daily Conversation Summary

Daily summaries are opt-in and scoped to one configured guild and text channel; announcements, moderation, and server settings remain public multi-server features isolated by `guild_id`. Only human-authored text is collected. Bot, webhook, system, and empty messages are excluded.

After startup, a background backfill reads Discord history from midnight in `Asia/Seoul` through the current time. Unique message IDs make the operation idempotent, and permission or API failures do not stop the rest of the bot. At 00:02, the previous day is aggregated and, when minimum activity thresholds are met, Gemini creates an overall summary and per-user summaries for a public report channel. Raw text is retained for three days by default.

The `/하루요약 상태`, `오늘`, `어제`, and `연결확인` command responses are private to the administrator who invoked them. The connection check sends one minimal Gemini request without revealing the API key or changing report state. Only the completed report body is posted publicly. See [the daily-summary operations guide](docs/daily-summary.md) for configuration, privacy, and failure handling.

## Demo

Demo media can be added later under `docs/assets/` as `announcement-demo.gif` and `moderation-demo.gif`. They are not embedded yet, so the README does not show broken images.

## Architecture

```text
Discord interactions/events
        │
        ├── cogs/              command and event adapters
        ├── services/          testable content and matching rules
        ├── tasks/             persistent announcement scheduler
        └── database/          async models and repositories
                 │
          SQLite / PostgreSQL
```

Discord-specific network work stays in Cogs and the scheduler. Text normalization, matching, classification, truncation, permissions, links, and schedule math are independently testable.

## Commands

| Command | Access | Purpose |
| --- | --- | --- |
| Apps → `공지로 등록` | Owner/Admin | Register an existing message |
| `/공지 등록` | Owner/Admin | Create and register a source message |
| `/공지 목록` | Owner/Admin | List active notices with previews and links |
| `/공지 삭제` | Owner/Admin | Delete a notice using autocomplete |
| `/공지 즉시전송` | Owner/Admin | Send a reminder now |
| `/금지어 추가` | Owner/Admin | Add a forbidden word |
| `/금지어 일괄추가` | Owner/Admin | Add up to 500 comma/newline-separated words |
| `/금지어 삭제` | Owner/Admin | Remove a word using autocomplete |
| `/금지어 목록` | Everyone | List registered words |
| `/설정 로그채널` | Owner/Admin | Select an existing audit-log channel |
| `/하루요약 상태` | Configured guild Owner/Admin | Inspect summary configuration and status |
| `/하루요약 오늘` | Configured guild Owner/Admin | Generate today's private preview |
| `/하루요약 어제` | Configured guild Owner/Admin | Generate yesterday's report manually |
| `/하루요약 연결확인` | Configured guild Owner/Admin | Privately verify Gemini model access |

Management responses are ephemeral. Ordinary message moderation cannot use ephemeral responses, so it uses DM then a temporary channel fallback.

## Project Structure

```text
src/eslee_bot/
├── bot.py                 # lifecycle, intents, and command sync
├── config.py              # validated environment settings
├── cogs/                  # announcement, moderation, settings, summary adapters
├── database/              # SQLAlchemy models, session, repositories
├── services/              # business and presentation rules
├── tasks/                 # announcement and daily-summary schedulers
└── utils/                 # permissions, text, time, message links
tests/                     # pure logic and async persistence tests
.github/workflows/ci.yml   # Ruff and pytest
```

## Tech Stack

- Python 3.12+
- discord.py 2.7.1
- SQLAlchemy 2.0.51 async ORM
- SQLite and aiosqlite
- PostgreSQL and asyncpg
- Google Gen AI SDK
- pydantic-settings and `.env`
- pytest, pytest-asyncio, and Ruff
- Docker and GitHub Actions

## Installation

```bash
git clone https://github.com/esleeeeee/eslee-discord-bot.git
cd eslee-discord-bot
python -m venv .venv
```

Activate the virtual environment, then install:

```bash
python -m pip install -e ".[dev]"
cp .env.example .env
```

On PowerShell, copy the file with `Copy-Item .env.example .env`.

## Discord Developer Portal Setup

1. Create an application at the Discord Developer Portal.
2. Open **Bot**, create the bot user, and copy its token into `.env`.
3. Enable **Message Content Intent** under Privileged Gateway Intents.
4. Enable **Public Bot** when other users should be able to install it in their servers.
5. Use OAuth2 URL Generator with the `bot` and `applications.commands` scopes.
6. Grant only the permissions listed below and invite the bot.

Never commit the token. If a token is exposed, reset it immediately in the portal.

## Required Intents

- Guilds
- Guild Messages
- Message Content (privileged; must be enabled in the Developer Portal)

The Members intent is not required.

## Required Bot Permissions

Required:

- View Channels
- Send Messages
- Manage Messages
- Read Message History
- Embed Links
- Use Application Commands (through the OAuth scope)

`Attach Files` is not needed because reminders never re-upload attachments. Do not grant the bot Discord Administrator.

## Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | — | Secret bot token |
| `DISCORD_DEV_GUILD_ID` | No | empty | Development-only fast sync to one test guild |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./data/eslee_bot.db` | SQLite or PostgreSQL connection URL |
| `LOG_LEVEL` | No | `INFO` | Standard Python log level |
| `SCHEDULER_POLL_SECONDS` | No | `60` | Due-check interval, 10–300 seconds |

Daily summaries additionally use `DAILY_SUMMARY_ENABLED`, the configured guild/source/report channel IDs, `GEMINI_API_KEY`, and optional model, timezone, threshold, and retention settings. These settings enable only the summary feature; the bot itself does not require a production guild ID. Copy the exact keys from [.env.example](.env.example).

No guild ID is required. Production always uses global commands and should leave `DISCORD_DEV_GUILD_ID` unset. When this optional value is set for local development, global sync still runs and a fast-updating copy is also synchronized to the specified test guild. `DISCORD_GUILD_ID`, `GUILD_ID`, and `TEST_GUILD_ID` are not used.

## Running Locally

```bash
python -m eslee_bot
```

The first run creates `data/eslee_bot.db`. Missing or invalid required environment values produce a readable startup error without exposing secrets.

PostgreSQL URLs beginning with `postgresql://` or `postgres://` are automatically normalized to SQLAlchemy's `postgresql+asyncpg://` async dialect. Northflank's `sslmode=require` query option is translated to asyncpg's supported `ssl=require` option while preserving the TLS requirement.

## Docker

```bash
cp .env.example .env
# Edit .env, then:
docker compose up --build -d
docker compose logs -f bot
```

The image runs as a non-root user. Compose mounts the named `bot-data` volume at `/app/data`, preserving SQLite data across container replacement without host bind-mount ownership problems.

## Testing

```bash
python -m ruff check .
python -m pytest
```

Tests cover exact and obfuscated moderation with false-positive guards, content classification and truncation, six-hour and overdue schedule math, permissions, guild isolation, daily-summary collection and startup backfill, Gemini error handling, and privacy-minimized persistence.

## CI

GitHub Actions runs on pushes and pull requests using Python 3.12. It installs the development extras, runs Ruff, then runs pytest.

## Security

- Tokens are loaded from `.env`, which is ignored by Git.
- No token or user message body is written to application logs.
- The bot neither requests nor requires Discord Administrator.
- Discord rate limits are left to `discord.py`; no bypass is attempted.
- User-provided announcement text cannot generate mentions when the bot creates its source message.

## Privacy

The moderation audit channel may display the original violating message to server administrators. SQLite stores the guild, user, and channel IDs, matched words, and timestamp—but not the original message body. Choose a restricted audit channel and define an appropriate retention policy for your server.

## Roadmap

- Configurable reminder intervals and active date ranges
- Quiet hours
- Role-based managers and escalating actions

These are deliberately outside v1.

The v1 deployment assumes one running bot process. Running multiple replicas against the same SQLite file can duplicate external Discord actions. Schema migrations are also not included yet; back up the database before changing models.

## License

MIT. The current copyright label is `eslee`; update it if needed before publishing.

Korean setup and operations documentation is available in [README.md](README.md).
