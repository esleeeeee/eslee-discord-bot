from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from eslee_bot.onekey_api import (
    MAX_REQUESTED_GUILD_IDS,
    OneKeyApiServer,
    cached_voice_state,
    find_voice_status,
    intersect_guild_ids,
    parse_requested_guild_ids,
)

TARGET_USER_ID = 123456789012345678
# At least as long as ONEKEY_API_TOKEN_MIN_LENGTH so tests use a realistic token.
API_TOKEN = "secure-test-token-value-0123456789abcdef"


@dataclass
class FakeBot:
    guilds: list[FakeGuild]
    ready: bool = True

    def is_ready(self) -> bool:
        return self.ready


class FakeGuild:
    """Mimics the parts of discord.Guild the voice lookup actually uses.

    discord.Guild exposes no public voice-state mapping, so a fake that invents
    one would let a broken lookup pass. This mirrors the real shape instead:
    a private _voice_state_for accessor plus an optional member cache.
    """

    def __init__(
        self,
        guild_id: int,
        *,
        voice_states: dict[int, object] | None = None,
        members: dict[int, object] | None = None,
    ) -> None:
        self.id = guild_id
        self._voice_states = dict(voice_states or {})
        self._members = dict(members or {})

    def get_member(self, user_id: int) -> object | None:
        return self._members.get(user_id)

    def _voice_state_for(self, user_id: int) -> object | None:
        return self._voice_states.get(user_id)


def voice_state(*, connected: bool = True) -> SimpleNamespace:
    channel = SimpleNamespace(id=987654321, name="General") if connected else None
    return SimpleNamespace(channel=channel)


def guild(guild_id: int, *, user_id: int | None = None, cache_member: bool = False) -> FakeGuild:
    """A guild whose voice state is only reachable the way discord.py stores it."""
    if user_id is None:
        return FakeGuild(guild_id)
    state = voice_state()
    members = {user_id: SimpleNamespace(voice=state)} if cache_member else None
    return FakeGuild(guild_id, voice_states={user_id: state}, members=members)


def disconnected_guild(guild_id: int, user_id: int) -> FakeGuild:
    """A guild that remembers the user but with no current channel."""
    return FakeGuild(guild_id, voice_states={user_id: voice_state(connected=False)})


def build_server(
    guilds: list[FakeGuild] | None = None,
    *,
    ready: bool = True,
    port: int = 8080,
) -> OneKeyApiServer:
    return OneKeyApiServer(
        bot=FakeBot(guilds or [], ready=ready),
        target_user_id=TARGET_USER_ID,
        api_token=API_TOKEN,
        port=port,
    )


@asynccontextmanager
async def api_client(server: OneKeyApiServer) -> AsyncIterator[TestClient]:
    """Drive the real aiohttp application through its router."""
    client = TestClient(TestServer(server.application))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def response_json(response: web.Response) -> dict[str, object]:
    body = response.body
    assert body is not None
    return json.loads(body.decode("utf-8"))


def test_find_voice_status_when_user_is_not_in_voice() -> None:
    status = find_voice_status([guild(1), guild(2)], TARGET_USER_ID)

    assert status.response_body() == {"in_voice": False}


def test_find_voice_status_across_multiple_guilds() -> None:
    status = find_voice_status([guild(1), guild(2, user_id=TARGET_USER_ID)], TARGET_USER_ID)

    # The location of the user is deliberately not disclosed.
    assert status.response_body() == {"in_voice": True}


def test_a_remembered_but_disconnected_voice_state_is_not_in_voice() -> None:
    status = find_voice_status([disconnected_guild(1, TARGET_USER_ID)], TARGET_USER_ID)

    assert status.response_body() == {"in_voice": False}


def test_the_lookup_targets_an_api_that_discord_guild_really_has() -> None:
    """Guards against fakes drifting from discord.py.

    Guild has never had a public voice_states mapping, so reading one would make
    every production lookup silently return "not in voice" while mocked tests
    still passed.
    """
    import discord

    assert not hasattr(discord.Guild, "voice_states")
    assert hasattr(discord.Guild, "_voice_state_for")
    assert hasattr(discord.Guild, "get_member")
    assert hasattr(discord.Member, "voice")


def test_voice_state_is_found_without_the_members_intent() -> None:
    """The bot does not request the members intent, so get_member returns None."""
    uncached = guild(1, user_id=TARGET_USER_ID, cache_member=False)

    assert uncached.get_member(TARGET_USER_ID) is None
    assert cached_voice_state(uncached, TARGET_USER_ID) is not None
    assert find_voice_status([uncached], TARGET_USER_ID).response_body() == {"in_voice": True}


def test_voice_state_is_found_through_the_member_when_it_is_cached() -> None:
    cached = guild(1, user_id=TARGET_USER_ID, cache_member=True)

    assert cached.get_member(TARGET_USER_ID) is not None
    assert find_voice_status([cached], TARGET_USER_ID).response_body() == {"in_voice": True}


def test_a_guild_exposing_neither_route_is_treated_as_not_in_voice() -> None:
    assert cached_voice_state(SimpleNamespace(id=1), TARGET_USER_ID) is None


def test_voice_status_never_carries_guild_or_channel_identifiers() -> None:
    status = find_voice_status([guild(2, user_id=TARGET_USER_ID)], TARGET_USER_ID)

    assert set(status.response_body()) == {"in_voice"}
    serialized = json.dumps(status.response_body())
    for leaked in ("987654321", "General", "guild", "channel"):
        assert leaked not in serialized


@pytest.mark.asyncio
async def test_health_distinguishes_process_health_from_discord_readiness() -> None:
    server = build_server(ready=False)

    response = await server.health(make_mocked_request("GET", "/health"))

    assert response.status == 200
    assert response_json(response) == {"status": "ok", "discord_ready": False}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [None, "Basic credentials", "Bearer", "Bearer wrong-token"],
)
async def test_voice_status_rejects_missing_or_invalid_authorization(
    authorization: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    server = build_server()

    with caplog.at_level(logging.DEBUG):
        response = await server.voice_status(
            make_mocked_request("GET", "/api/voice-status", headers=headers)
        )

    assert response.status == 401
    assert response_json(response) == {"error": "unauthorized"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Vary"] == "Authorization"
    assert API_TOKEN not in caplog.text
    assert authorization not in caplog.text if authorization else True


@pytest.mark.asyncio
async def test_voice_status_returns_503_until_discord_is_ready() -> None:
    server = build_server(ready=False)

    response = await server.voice_status(
        make_mocked_request(
            "GET",
            "/api/voice-status",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )
    )

    assert response.status == 503
    assert response_json(response) == {"error": "discord_not_ready"}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_voice_status_returns_cached_voice_state_for_valid_token() -> None:
    server = build_server([guild(1, user_id=TARGET_USER_ID)])

    response = await server.voice_status(
        make_mocked_request(
            "GET",
            "/api/voice-status",
            headers={"Authorization": f"bearer {API_TOKEN}"},
        )
    )

    assert response.status == 200
    assert response_json(response) == {"in_voice": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guilds", "expected"),
    [([], False), ([guild(1, user_id=TARGET_USER_ID)], True)],
)
async def test_routed_voice_status_returns_only_the_boolean(
    guilds: list[FakeGuild],
    expected: bool,
) -> None:
    server = build_server(guilds)

    async with api_client(server) as client:
        response = await client.get(
            "/api/voice-status",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )

        assert response.status == 200
        assert await response.json() == {"in_voice": expected}
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Vary"] == "Authorization"


@pytest.mark.asyncio
async def test_routed_voice_status_requires_the_bearer_token() -> None:
    server = build_server([guild(1, user_id=TARGET_USER_ID)])

    async with api_client(server) as client:
        response = await client.get("/api/voice-status")

        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.headers["Vary"] == "Authorization"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credential",
    [
        "한글토큰입니다",
        "\U0001f600token",
        "tokén-with-accents",
        "Bearer",
        "",
    ],
    ids=["hangul", "emoji", "accented", "no-credential", "empty"],
)
async def test_an_unusable_credential_is_rejected_rather_than_crashing(
    credential: str,
) -> None:
    """compare_digest raises on non-ASCII str, which would turn 401 into a 500."""
    server = build_server([guild(1, user_id=TARGET_USER_ID)])
    header = f"Bearer {credential}" if credential else "Bearer"

    async with api_client(server) as client:
        response = await client.get("/api/voice-status", headers={"Authorization": header})

        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_credential",
    [b"caf\xe9-token", b"\xff\xfe\x80", API_TOKEN.encode("utf-8") + b"\xff"],
    ids=["latin1-byte", "raw-bytes", "valid-token-plus-junk"],
)
async def test_a_header_that_is_not_valid_utf8_is_rejected_not_crashed(
    raw_credential: bytes,
) -> None:
    """Neither an HTTP client nor make_mocked_request can carry these.

    aiohttp decodes header bytes with surrogateescape, so invalid UTF-8 on the
    wire reaches the handler as a lone surrogate. A plain .encode("utf-8") on
    that raises, which aiohttp turns into a 500 with a traceback for any
    anonymous caller, so the rejection has to survive it. Both test helpers
    re-encode headers as strict UTF-8 and would raise before reaching the
    handler, so this drives it with a request stub carrying only the header.
    """
    credential = raw_credential.decode("utf-8", "surrogateescape")
    server = build_server([guild(1, user_id=TARGET_USER_ID)])
    request = SimpleNamespace(headers={"Authorization": f"Bearer {credential}"})

    response = await server.voice_status(request)  # type: ignore[arg-type]

    assert response.status == 401
    assert response_json(response) == {"error": "unauthorized"}


@pytest.mark.asyncio
async def test_routed_health_needs_no_authentication() -> None:
    server = build_server()

    async with api_client(server) as client:
        response = await client.get("/health")

        assert response.status == 200
        assert await response.json() == {"status": "ok", "discord_ready": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/", "/api", "/api/voice-status/extra", "/api/guild-intersection/extra", "/metrics"],
)
async def test_only_the_documented_routes_are_served(path: str) -> None:
    server = build_server()

    async with api_client(server) as client:
        response = await client.get(path, headers={"Authorization": f"Bearer {API_TOKEN}"})

        assert response.status == 404


@pytest.mark.asyncio
async def test_voice_status_rejects_other_http_methods() -> None:
    server = build_server()

    async with api_client(server) as client:
        response = await client.post(
            "/api/voice-status",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )

        assert response.status == 405


@pytest.mark.asyncio
async def test_start_is_idempotent_and_reuses_one_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[web.AppRunner] = []
    original = web.AppRunner

    def spy(*args: object, **kwargs: object) -> web.AppRunner:
        runner = original(*args, **kwargs)  # type: ignore[arg-type]
        created.append(runner)
        return runner

    monkeypatch.setattr("eslee_bot.onekey_api.web.AppRunner", spy)
    # Port 0 lets the OS pick a free port so the test never collides.
    server = build_server(port=0)

    try:
        await server.start()
        await server.start()

        assert server.is_running is True
        assert len(created) == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_close_releases_the_port_and_tolerates_being_called_twice() -> None:
    # Bind an ephemeral port first, then demand that exact port back after
    # closing: port 0 alone would hand out a fresh port and hide a leak.
    probe = build_server(port=0)
    await probe.start()
    port = probe.bound_port
    assert port is not None
    await probe.close()

    server = build_server(port=port)
    await server.start()
    assert server.is_running is True
    assert server.bound_port == port

    await server.close()
    assert server.is_running is False
    assert server.bound_port is None
    await server.close()
    assert server.is_running is False

    # The port really was released, so the same one binds again.
    await server.start()
    assert server.bound_port == port
    await server.close()


@pytest.mark.asyncio
async def test_closing_a_server_that_never_started_is_a_no_op() -> None:
    server = build_server(port=0)

    await server.close()

    assert server.is_running is False


@pytest.mark.asyncio
async def test_a_failed_bind_cleans_up_and_leaves_the_server_startable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned: list[bool] = []
    original_cleanup = web.AppRunner.cleanup

    async def spy_cleanup(self: web.AppRunner) -> None:
        cleaned.append(True)
        await original_cleanup(self)

    async def refuse_to_bind(self: web.TCPSite) -> None:
        raise OSError("address already in use")

    monkeypatch.setattr("eslee_bot.onekey_api.web.AppRunner.cleanup", spy_cleanup)
    monkeypatch.setattr("eslee_bot.onekey_api.web.TCPSite.start", refuse_to_bind)
    server = build_server(port=0)

    with pytest.raises(OSError):
        await server.start()

    assert cleaned == [True]
    assert server.is_running is False

    monkeypatch.undo()
    await server.start()
    assert server.is_running is True
    await server.close()


# --- POST /api/guild-intersection ------------------------------------------
# The property this endpoint rests on is that the response is always a subset of
# the request, so the bot can confirm a guild the caller already knows but can
# never disclose one it does not.

GUILD_A = "111111111111111111"
GUILD_B = "222222222222222222"
GUILD_UNKNOWN = "999999999999999999"


def intersection_server(*guild_ids: str, ready: bool = True) -> OneKeyApiServer:
    guilds = [FakeGuild(int(guild_id)) for guild_id in guild_ids]
    return OneKeyApiServer(
        bot=FakeBot(guilds, ready=ready),
        target_user_id=TARGET_USER_ID,
        api_token=API_TOKEN,
        port=0,
    )


async def post_intersection(client, body, *, authorized=True, raw=None):
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if authorized else {}
    if raw is not None:
        headers["Content-Type"] = "application/json"
        return await client.post("/api/guild-intersection", data=raw, headers=headers)
    return await client.post("/api/guild-intersection", json=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bot_guilds", "requested", "expected"),
    [
        ((GUILD_A, GUILD_B), [GUILD_A, GUILD_B], [GUILD_A, GUILD_B]),
        ((GUILD_A, GUILD_B), [GUILD_B], [GUILD_B]),
        ((GUILD_A,), [GUILD_A, GUILD_UNKNOWN], [GUILD_A]),
        ((GUILD_A,), [GUILD_UNKNOWN], []),
        ((GUILD_A,), [], []),
        ((), [GUILD_A], []),
    ],
    ids=["all-shared", "one-shared", "partial", "none-shared", "empty-request", "bot-in-none"],
)
async def test_intersection_returns_only_the_requested_ids_the_bot_shares(
    bot_guilds: tuple[str, ...],
    requested: list[str],
    expected: list[str],
) -> None:
    server = intersection_server(*bot_guilds)

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": requested})

        assert response.status == 200
        body = await response.json()
        assert body == {"guild_ids": expected}
        assert set(body) == {"guild_ids"}


@pytest.mark.asyncio
async def test_the_response_is_always_a_subset_of_the_request() -> None:
    """The invariant the whole design rests on.

    Asserted as a subset relation rather than against a fixture, so it still
    fails if anyone later turns this into an enumeration of the bot's guilds.
    """
    server = intersection_server(GUILD_A, GUILD_B, GUILD_UNKNOWN)

    async with api_client(server) as client:
        for requested in ([], [GUILD_A], [GUILD_B, GUILD_A], [GUILD_A, "123"]):
            response = await post_intersection(client, {"guild_ids": requested})

            assert response.status == 200
            returned = (await response.json())["guild_ids"]
            assert set(returned) <= set(requested)


@pytest.mark.asyncio
async def test_a_guild_the_caller_did_not_send_is_never_disclosed() -> None:
    # The bot is in GUILD_B, but the caller never names it, so it must not leak.
    server = intersection_server(GUILD_A, GUILD_B)

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": [GUILD_A]})

        assert response.status == 200
        raw = await response.text()
        assert GUILD_B not in raw
        assert (await response.json()) == {"guild_ids": [GUILD_A]}


@pytest.mark.asyncio
async def test_intersection_never_carries_names_channels_or_user_data() -> None:
    server = intersection_server(GUILD_A)

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": [GUILD_A, GUILD_UNKNOWN]})

        raw = await response.text()
        for leaked in ("name", "channel", "General", "user", str(TARGET_USER_ID)):
            assert leaked not in raw


@pytest.mark.asyncio
async def test_duplicate_requested_ids_appear_once_in_caller_order() -> None:
    server = intersection_server(GUILD_A, GUILD_B)

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": [GUILD_B, GUILD_A, GUILD_B]})

        assert (await response.json()) == {"guild_ids": [GUILD_B, GUILD_A]}


@pytest.mark.asyncio
async def test_intersection_accepts_exactly_the_maximum_number_of_ids() -> None:
    server = intersection_server(GUILD_A)
    requested = [str(900000000000000000 + index) for index in range(MAX_REQUESTED_GUILD_IDS - 1)]
    requested.append(GUILD_A)

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": requested})

        assert response.status == 200
        assert (await response.json()) == {"guild_ids": [GUILD_A]}


@pytest.mark.asyncio
async def test_intersection_rejects_more_than_the_maximum_number_of_ids() -> None:
    server = intersection_server(GUILD_A)
    requested = [str(900000000000000000 + index) for index in range(MAX_REQUESTED_GUILD_IDS + 1)]

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": requested})

        assert response.status == 400
        assert (await response.json())["error"] == "invalid_guild_ids"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"guild_ids": [111111111111111111]},
        {"guild_ids": [True]},
        {"guild_ids": [None]},
        {"guild_ids": [GUILD_A, 222222222222222222]},
        {"guild_ids": ["not-a-number"]},
        {"guild_ids": ["12345678901234567890123"]},
        {"guild_ids": [""]},
        {"guild_ids": GUILD_A},
        {"guild_ids": {"0": GUILD_A}},
        {"guild_ids": None},
        {"other": [GUILD_A]},
        [GUILD_A],
        "guild_ids",
        5,
    ],
    ids=[
        "int-id",
        "bool-id",
        "null-id",
        "mixed-int",
        "non-numeric",
        "too-long",
        "empty-string",
        "bare-string-not-list",
        "object-not-list",
        "null-list",
        "missing-key",
        "top-level-array",
        "top-level-string",
        "top-level-number",
    ],
)
async def test_intersection_rejects_malformed_guild_ids(body: object) -> None:
    server = intersection_server(GUILD_A)

    async with api_client(server) as client:
        response = await post_intersection(client, body)

        assert response.status == 400
        assert (await response.json())["error"] == "invalid_guild_ids"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    ["", "{", "not json", '{"guild_ids": [', '{"guild_ids": ["1",]}'],
    ids=["empty", "truncated", "plain-text", "unclosed-array", "trailing-comma"],
)
async def test_intersection_rejects_invalid_json(raw: str) -> None:
    server = intersection_server(GUILD_A)

    async with api_client(server) as client:
        response = await post_intersection(client, None, raw=raw)

        assert response.status == 400
        assert (await response.json())["error"] == "invalid_json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [None, "Basic credentials", "Bearer", "Bearer wrong-token"],
)
async def test_intersection_rejects_missing_or_invalid_authorization(
    authorization: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    server = intersection_server(GUILD_A)

    async with api_client(server) as client:
        with caplog.at_level(logging.DEBUG):
            response = await client.post(
                "/api/guild-intersection",
                json={"guild_ids": [GUILD_A]},
                headers=headers,
            )

        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Vary"] == "Authorization"
        assert API_TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_intersection_rejects_an_unauthorized_caller_before_validating() -> None:
    # Auth gates everything, so an anonymous caller learns nothing from the
    # difference between a malformed and a well-formed body.
    server = intersection_server(GUILD_A)

    async with api_client(server) as client:
        response = await post_intersection(client, {"nonsense": True}, authorized=False)

        assert response.status == 401


@pytest.mark.asyncio
async def test_intersection_returns_503_until_discord_is_ready() -> None:
    # An empty intersection during startup would read as a truthful
    # "none of your guilds", so it must be refused instead.
    server = intersection_server(GUILD_A, ready=False)

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": [GUILD_A]})

        assert response.status == 503
        assert await response.json() == {"error": "discord_not_ready"}
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_intersection_carries_the_same_cache_headers_as_voice_status() -> None:
    server = intersection_server(GUILD_A)

    async with api_client(server) as client:
        response = await post_intersection(client, {"guild_ids": [GUILD_A]})

        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Vary"] == "Authorization"


@pytest.mark.asyncio
async def test_intersection_rejects_other_http_methods() -> None:
    server = intersection_server(GUILD_A)

    async with api_client(server) as client:
        response = await client.get(
            "/api/guild-intersection",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )

        assert response.status == 405


@pytest.mark.asyncio
async def test_adding_the_intersection_route_does_not_regress_the_existing_ones() -> None:
    server = intersection_server(GUILD_A)
    auth = {"Authorization": f"Bearer {API_TOKEN}"}

    async with api_client(server) as client:
        health = await client.get("/health")
        assert health.status == 200
        assert await health.json() == {"status": "ok", "discord_ready": True}

        voice = await client.get("/api/voice-status", headers=auth)
        assert voice.status == 200
        assert await voice.json() == {"in_voice": False}
        assert voice.headers["Cache-Control"] == "no-store"
        assert voice.headers["Vary"] == "Authorization"


def test_parse_requested_guild_ids_accepts_a_well_formed_list() -> None:
    assert parse_requested_guild_ids({"guild_ids": [GUILD_A, GUILD_B]}) == [GUILD_A, GUILD_B]


def test_intersect_guild_ids_is_a_pure_subset_of_its_input() -> None:
    guilds = [FakeGuild(int(GUILD_A)), FakeGuild(int(GUILD_B))]

    matched = intersect_guild_ids(guilds, [GUILD_A, GUILD_UNKNOWN])

    assert matched == [GUILD_A]
    assert set(matched) <= {GUILD_A, GUILD_UNKNOWN}
