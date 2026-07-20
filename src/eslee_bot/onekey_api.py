from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from hmac import compare_digest
from typing import Any, Protocol

from aiohttp import web

logger = logging.getLogger(__name__)


class DiscordStateProvider(Protocol):
    @property
    def guilds(self) -> Iterable[Any]: ...

    def is_ready(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class VoiceStatus:
    in_voice: bool
    guild_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None

    def response_body(self) -> dict[str, bool | str]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def find_voice_status(guilds: Iterable[Any], target_user_id: int) -> VoiceStatus:
    """Find a cached guild voice state without making Discord REST requests."""
    for guild in guilds:
        voice_states: Mapping[int, Any] = getattr(guild, "voice_states", {})
        voice_state = voice_states.get(target_user_id)
        channel = getattr(voice_state, "channel", None)
        if channel is None:
            continue
        return VoiceStatus(
            in_voice=True,
            guild_id=str(guild.id),
            channel_id=str(channel.id),
            channel_name=str(channel.name),
        )
    return VoiceStatus(in_voice=False)


class OneKeyApiServer:
    def __init__(
        self,
        *,
        bot: DiscordStateProvider,
        target_user_id: int,
        api_token: str,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self._bot = bot
        self._target_user_id = target_user_id
        self._api_token = api_token
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self.application = web.Application()
        self.application.router.add_get("/health", self.health)
        self.application.router.add_get("/api/voice-status", self.voice_status)

    async def start(self) -> None:
        if self._runner is not None:
            return
        runner = web.AppRunner(self.application, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, self._host, self._port)
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner
        logger.info("OneKey API listening on %s:%s", self._host, self._port)

    async def close(self) -> None:
        if self._runner is None:
            return
        runner, self._runner = self._runner, None
        await runner.cleanup()
        logger.info("OneKey API stopped")

    async def health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "status": "ok",
                "discord_ready": self._bot.is_ready(),
            }
        )

    async def voice_status(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response(
                {"error": "unauthorized"},
                status=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not self._bot.is_ready():
            return web.json_response({"error": "discord_not_ready"}, status=503)
        status = find_voice_status(self._bot.guilds, self._target_user_id)
        return web.json_response(status.response_body())

    def _is_authorized(self, request: web.Request) -> bool:
        authorization = request.headers.get("Authorization", "")
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return compare_digest(parts[1], self._api_token)
