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

from eslee_bot.onekey_api import OneKeyApiServer, find_voice_status

TARGET_USER_ID = 123456789012345678
# At least as long as ONEKEY_API_TOKEN_MIN_LENGTH so tests use a realistic token.
API_TOKEN = "secure-test-token-value-0123456789abcdef"


@dataclass
class FakeBot:
    guilds: list[SimpleNamespace]
    ready: bool = True

    def is_ready(self) -> bool:
        return self.ready


def guild(guild_id: int, *, user_id: int | None = None) -> SimpleNamespace:
    voice_states = {}
    if user_id is not None:
        channel = SimpleNamespace(id=987654321, name="General")
        voice_states[user_id] = SimpleNamespace(channel=channel)
    return SimpleNamespace(id=guild_id, voice_states=voice_states)


def disconnected_guild(guild_id: int, user_id: int) -> SimpleNamespace:
    """A guild that remembers the user but with no current channel."""
    return SimpleNamespace(id=guild_id, voice_states={user_id: SimpleNamespace(channel=None)})


def build_server(
    guilds: list[SimpleNamespace] | None = None,
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
    guilds: list[SimpleNamespace],
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
async def test_routed_health_needs_no_authentication() -> None:
    server = build_server()

    async with api_client(server) as client:
        response = await client.get("/health")

        assert response.status == 200
        assert await response.json() == {"status": "ok", "discord_ready": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/api", "/api/voice-status/extra", "/metrics"])
async def test_only_the_two_documented_routes_are_served(path: str) -> None:
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
async def test_close_releases_the_runner_and_tolerates_being_called_twice() -> None:
    server = build_server(port=0)

    await server.start()
    assert server.is_running is True

    await server.close()
    assert server.is_running is False
    await server.close()
    assert server.is_running is False

    # A clean shutdown must leave the server startable again.
    await server.start()
    assert server.is_running is True
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
