from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from eslee_bot.onekey_api import OneKeyApiServer, find_voice_status

TARGET_USER_ID = 123456789012345678
API_TOKEN = "secure-test-token-value"


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


def response_json(response: web.Response) -> dict[str, object]:
    body = response.body
    assert body is not None
    return json.loads(body.decode("utf-8"))


def test_find_voice_status_when_user_is_not_in_voice() -> None:
    status = find_voice_status([guild(1), guild(2)], TARGET_USER_ID)

    assert status.response_body() == {"in_voice": False}


def test_find_voice_status_across_multiple_guilds() -> None:
    status = find_voice_status([guild(1), guild(2, user_id=TARGET_USER_ID)], TARGET_USER_ID)

    assert status.response_body() == {
        "in_voice": True,
        "guild_id": "2",
        "channel_id": "987654321",
        "channel_name": "General",
    }


@pytest.mark.asyncio
async def test_health_distinguishes_process_health_from_discord_readiness() -> None:
    server = OneKeyApiServer(
        bot=FakeBot([], ready=False),
        target_user_id=TARGET_USER_ID,
        api_token=API_TOKEN,
    )

    response = await server.health(make_mocked_request("GET", "/health"))

    assert response.status == 200
    assert response_json(response) == {"status": "ok", "discord_ready": False}


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
    server = OneKeyApiServer(
        bot=FakeBot([]),
        target_user_id=TARGET_USER_ID,
        api_token=API_TOKEN,
    )

    with caplog.at_level(logging.DEBUG):
        response = await server.voice_status(
            make_mocked_request("GET", "/api/voice-status", headers=headers)
        )

    assert response.status == 401
    assert response_json(response) == {"error": "unauthorized"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert API_TOKEN not in caplog.text
    assert authorization not in caplog.text if authorization else True


@pytest.mark.asyncio
async def test_voice_status_returns_503_until_discord_is_ready() -> None:
    server = OneKeyApiServer(
        bot=FakeBot([], ready=False),
        target_user_id=TARGET_USER_ID,
        api_token=API_TOKEN,
    )

    response = await server.voice_status(
        make_mocked_request(
            "GET",
            "/api/voice-status",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )
    )

    assert response.status == 503
    assert response_json(response) == {"error": "discord_not_ready"}


@pytest.mark.asyncio
async def test_voice_status_returns_cached_voice_state_for_valid_token() -> None:
    server = OneKeyApiServer(
        bot=FakeBot([guild(1, user_id=TARGET_USER_ID)]),
        target_user_id=TARGET_USER_ID,
        api_token=API_TOKEN,
    )

    response = await server.voice_status(
        make_mocked_request(
            "GET",
            "/api/voice-status",
            headers={"Authorization": f"bearer {API_TOKEN}"},
        )
    )

    assert response.status == 200
    assert response_json(response)["in_voice"] is True
