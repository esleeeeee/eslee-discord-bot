from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from hmac import compare_digest
from typing import Any, Protocol

from aiohttp import web

logger = logging.getLogger(__name__)

NO_STORE = "no-store"

# A Discord user can belong to 100 guilds, or 200 with Nitro, and the caller is
# only ever meant to send the guilds it already knows. 200 is therefore the real
# domain maximum rather than an arbitrary ceiling.
MAX_REQUESTED_GUILD_IDS = 200
# 200 ids of ~20 digits plus JSON punctuation is about 4 KB; the rest is slack.
# aiohttp's 1 MiB default is ~250x more than this endpoint can ever need.
MAX_REQUEST_BODY_BYTES = 16 * 1024
# Snowflakes are unsigned 64-bit, so at most 20 digits. Strings only: a 19-digit
# snowflake exceeds 2**53, so a JSON number would be silently corrupted.
_SNOWFLAKE_PATTERN = re.compile(r"^[0-9]{1,20}$")


def _credential_bytes(value: str) -> bytes:
    """Encode a credential for constant-time comparison, never raising.

    compare_digest refuses str with non-ASCII characters, and a header value that
    decoded to a lone surrogate would break a plain utf-8 encode, so an
    unauthenticated request could turn a 401 into a 500 with a traceback in the
    log. surrogateescape round-trips whatever the parser produced.
    """
    return value.encode("utf-8", "surrogateescape")


class DiscordStateProvider(Protocol):
    @property
    def guilds(self) -> Iterable[Any]: ...

    def is_ready(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class VoiceStatus:
    """Whether the target user is in a voice channel, and nothing more.

    Guild, channel and channel-name are deliberately not carried: the caller only
    needs the boolean, so the server never has an opportunity to disclose which
    server or room the user is in.
    """

    in_voice: bool

    def response_body(self) -> dict[str, bool]:
        return {"in_voice": self.in_voice}


def cached_voice_state(guild: Any, target_user_id: int) -> Any:
    """Read one guild's cached voice state for a user, without any REST call.

    discord.Guild has no public voice-state mapping. It keeps them in the private
    ``_voice_states`` dict, filled from gateway events whenever the voice-states
    intent is on, and reads them back through ``_voice_state_for`` — which is
    exactly what ``Member.voice`` does. Preferring the member is the documented
    route, but the member cache needs the members intent that this bot does not
    request, so the accessor is required as well or genuinely-connected users
    would be reported as absent.
    """
    member = guild.get_member(target_user_id) if hasattr(guild, "get_member") else None
    state = getattr(member, "voice", None)
    if state is not None:
        return state
    accessor = getattr(guild, "_voice_state_for", None)
    if callable(accessor):
        return accessor(target_user_id)
    return None


def find_voice_status(guilds: Iterable[Any], target_user_id: int) -> VoiceStatus:
    """Find a cached guild voice state without making Discord REST requests."""
    for guild in guilds:
        if getattr(cached_voice_state(guild, target_user_id), "channel", None) is not None:
            return VoiceStatus(in_voice=True)
    return VoiceStatus(in_voice=False)


class GuildIdsError(ValueError):
    """The caller's guild id list was not usable."""


def parse_requested_guild_ids(payload: Any) -> list[str]:
    """Validate a caller-supplied guild id list, rejecting anything ambiguous.

    Strict about types on purpose. A bare string would otherwise iterate
    character by character and produce a silently empty intersection instead of
    an error, and bool is a subclass of int, so only str is accepted.
    """
    if not isinstance(payload, dict):
        raise GuildIdsError("body must be a JSON object")
    if "guild_ids" not in payload:
        raise GuildIdsError("guild_ids is required")
    raw = payload["guild_ids"]
    if not isinstance(raw, list):
        raise GuildIdsError("guild_ids must be an array of strings")
    if len(raw) > MAX_REQUESTED_GUILD_IDS:
        raise GuildIdsError(f"guild_ids must hold at most {MAX_REQUESTED_GUILD_IDS} entries")
    for item in raw:
        if not isinstance(item, str) or not _SNOWFLAKE_PATTERN.match(item):
            raise GuildIdsError("every guild id must be a string of 1-20 digits")
    return raw


def intersect_guild_ids(guilds: Iterable[Any], requested: list[str]) -> list[str]:
    """Return the requested ids the bot is also in, in the caller's order.

    The result is always a subset of `requested`: the bot's own ids are only ever
    used as a membership test, never as a source of output, so this can never
    disclose a guild the caller did not already name.
    """
    present = {str(guild.id) for guild in guilds}
    seen: set[str] = set()
    matched: list[str] = []
    for guild_id in requested:
        if guild_id in present and guild_id not in seen:
            seen.add(guild_id)
            matched.append(guild_id)
    return matched


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
        # Compared as bytes: compare_digest rejects str with non-ASCII characters,
        # so a unicode credential would otherwise raise instead of returning 401.
        self._api_token = _credential_bytes(api_token)
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self.application = web.Application(client_max_size=MAX_REQUEST_BODY_BYTES)
        self.application.router.add_get("/health", self.health)
        self.application.router.add_get("/api/voice-status", self.voice_status)
        self.application.router.add_post("/api/guild-intersection", self.guild_intersection)

    @property
    def is_running(self) -> bool:
        return self._runner is not None

    @property
    def bound_port(self) -> int | None:
        """The port actually bound, which differs from _port when PORT is 0."""
        addresses = self._runner.addresses if self._runner is not None else None
        return int(addresses[0][1]) if addresses else None

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
            },
            headers={"Cache-Control": NO_STORE},
        )

    async def voice_status(self, request: web.Request) -> web.Response:
        # Presence changes constantly and the body depends on the credential, so
        # no cache anywhere may keep or share a response.
        headers = {"Cache-Control": NO_STORE, "Vary": "Authorization"}
        if not self._is_authorized(request):
            return web.json_response(
                {"error": "unauthorized"},
                status=401,
                headers={**headers, "WWW-Authenticate": "Bearer"},
            )
        if not self._bot.is_ready():
            return web.json_response(
                {"error": "discord_not_ready"},
                status=503,
                headers=headers,
            )
        status = find_voice_status(self._bot.guilds, self._target_user_id)
        return web.json_response(status.response_body(), headers=headers)

    async def guild_intersection(self, request: web.Request) -> web.Response:
        """Confirm which of the caller's own guilds this bot is also in.

        The caller sends the guild ids it already knows locally, and only those
        are echoed back. The bot never enumerates its own guild list, so a
        caller cannot learn about a guild it did not already name — which is why
        this stays safe even though the shared token is not a real secret.
        """
        headers = {"Cache-Control": NO_STORE, "Vary": "Authorization"}
        if not self._is_authorized(request):
            return web.json_response(
                {"error": "unauthorized"},
                status=401,
                headers={**headers, "WWW-Authenticate": "Bearer"},
            )
        if not self._bot.is_ready():
            # An empty intersection during startup would read as a truthful
            # "none of your guilds", so refuse rather than answer wrongly.
            return web.json_response(
                {"error": "discord_not_ready"},
                status=503,
                headers=headers,
            )
        try:
            payload = await request.json()
        except ValueError:
            return web.json_response(
                {"error": "invalid_json"},
                status=400,
                headers=headers,
            )
        try:
            requested = parse_requested_guild_ids(payload)
        except GuildIdsError as error:
            return web.json_response(
                {"error": "invalid_guild_ids", "detail": str(error)},
                status=400,
                headers=headers,
            )
        matched = intersect_guild_ids(self._bot.guilds, requested)
        return web.json_response({"guild_ids": matched}, headers=headers)

    def _is_authorized(self, request: web.Request) -> bool:
        authorization = request.headers.get("Authorization", "")
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return compare_digest(_credential_bytes(parts[1]), self._api_token)
