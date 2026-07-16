from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from eslee_bot.database.migration import (
    MigrationError,
    migrate_sqlite_to_postgresql,
    print_migration_report,
    read_sqlite_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely merge SQLite data into PostgreSQL.")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=Path("data/eslee_bot.db"),
        help="Read-only SQLite source path (default: data/eslee_bot.db)",
    )
    parser.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL (default: DATABASE_URL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform inserts and verification, then roll back the PostgreSQL transaction",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        print(
            f"Set {args.database_url_env} to the target PostgreSQL URL.",
            file=sys.stderr,
        )
        return 2

    try:
        snapshot = read_sqlite_snapshot(args.sqlite_path)
        report = await migrate_sqlite_to_postgresql(
            snapshot,
            database_url,
            dry_run=args.dry_run,
        )
    except (MigrationError, OSError, SQLAlchemyError, sqlite3.Error) as error:
        print(f"Migration failed (PostgreSQL rolled back): {error}", file=sys.stderr)
        return 1

    print_migration_report(report)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
