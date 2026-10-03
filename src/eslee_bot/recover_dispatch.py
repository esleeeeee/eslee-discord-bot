"""Offline operator recovery. All bot instances MUST be stopped before resetting a claim."""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import update

from eslee_bot.database import Database
from eslee_bot.database.models import Announcement


async def reset_verified_unsent(
    database: Database,
    *,
    guild_id: int,
    announcement_id: int,
    dispatch_id: str,
    bots_stopped: bool,
    verified_not_delivered: bool,
) -> bool:
    if not bots_stopped or not verified_not_delivered:
        raise ValueError("Stop every bot instance and verify no reminder was delivered first")
    async with database.session_factory() as session:
        result = await session.execute(
            update(Announcement)
            .where(
                Announcement.id == announcement_id,
                Announcement.guild_id == guild_id,
                Announcement.dispatch_id == dispatch_id,
            )
            .values(dispatch_id=None, dispatch_started_at=None)
        )
        await session.commit()
        return bool(result.rowcount)


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--guild-id", required=True, type=int)
    parser.add_argument("--announcement-id", required=True, type=int)
    parser.add_argument("--dispatch-id", required=True)
    parser.add_argument("--bots-stopped", action="store_true")
    parser.add_argument("--verified-not-delivered", action="store_true")
    args = parser.parse_args()
    if not args.bots_stopped or not args.verified_not_delivered:
        parser.error("both safety confirmations are required; never run while a bot is active")
    database = Database(args.database_url)
    try:
        restored = await reset_verified_unsent(
            database,
            guild_id=args.guild_id,
            announcement_id=args.announcement_id,
            dispatch_id=args.dispatch_id,
            bots_stopped=args.bots_stopped,
            verified_not_delivered=args.verified_not_delivered,
        )
        print("Claim reset; resume bots to retry." if restored else "No matching claim; unchanged.")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(_main())
