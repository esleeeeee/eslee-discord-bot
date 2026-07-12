from __future__ import annotations

import sys

from pydantic import ValidationError

from eslee_bot.bot import EsleeBot
from eslee_bot.config import get_settings
from eslee_bot.logging_config import configure_logging


def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as error:
        print(
            f"환경설정 오류: DISCORD_TOKEN을 포함한 필수 값을 .env에 설정해 주세요.\n상세: {error}",
            file=sys.stderr,
        )
        raise SystemExit(2) from error

    configure_logging(settings.log_level)
    bot = EsleeBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
