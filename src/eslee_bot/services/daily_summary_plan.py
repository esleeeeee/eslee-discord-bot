"""Request planning, pacing and checkpoint state for daily summary generation.

The free Gemini tier allows only 20 requests per day, so a report must know how
many requests it needs *before* it spends the first one, must never repeat work
that already succeeded, and must spread requests out enough to stay under the
per-minute request and token ceilings.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from eslee_bot.services.daily_summary import (
    SummaryMessage,
    SummaryTarget,
    target_payload,
    transcript_payload,
)
from eslee_bot.services.daily_summary_retry import AIRequestFailure, AIRetryKind

# Google AI Studio free tier for the configured model.
FREE_TIER_RPM = 5
FREE_TIER_TPM = 250_000
FREE_TIER_RPD = 20

# Pace below the published ceilings so a burst never trips a 429.
PACING_REQUESTS_PER_MINUTE = 4
PACING_TOKENS_PER_MINUTE = 200_000

# A single request never carries more prompt characters than this. Combined with
# the pessimistic token estimate below it keeps one request far under the TPM
# ceiling while still being large enough that ordinary days need one request.
MAX_REQUEST_INPUT_CHARS = 100_000

# Deliberately pessimistic. Korean Discord transcripts tokenize at well under one
# token per character, so treating every character as a token over-estimates and
# therefore never under-books the quota.
CHARACTERS_PER_TOKEN = 1.0


def estimate_tokens(characters: int) -> int:
    return math.ceil(max(0, characters) / CHARACTERS_PER_TOKEN)


def _fingerprint(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ChunkPlan:
    index: int
    total: int
    transcript: str
    message_count: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SummaryPlan:
    chunks: tuple[ChunkPlan, ...]
    final_fingerprint: str
    transcript_characters: int
    message_count: int

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def uses_chunk_fallback(self) -> bool:
        return len(self.chunks) > 1

    @property
    def planned_requests(self) -> int:
        """Chunk requests plus the final consolidation request."""
        return 1 if len(self.chunks) <= 1 else len(self.chunks) + 1

    @property
    def estimated_input_tokens(self) -> int:
        return estimate_tokens(self.transcript_characters)


def build_summary_plan(
    messages: list[SummaryMessage],
    targets: list[SummaryTarget],
    timezone: Any,
    *,
    transcript_budget_characters: int,
) -> SummaryPlan:
    """Split a day into the fewest chunks that each fit one Gemini request."""
    budget = max(1, transcript_budget_characters)
    full_transcript = transcript_payload(messages, timezone)
    targets_key = target_payload(targets)

    if len(full_transcript) <= budget:
        groups = [(full_transcript, len(messages))]
    else:
        groups = []
        current: list[SummaryMessage] = []
        current_size = 0
        for message in messages:
            # transcript_payload of one message includes the enclosing brackets,
            # so this over-estimates and the real chunk always fits the budget.
            message_size = len(transcript_payload([message], timezone))
            if current and current_size + message_size > budget:
                groups.append((transcript_payload(current, timezone), len(current)))
                current = []
                current_size = 0
            current.append(message)
            current_size += message_size
        if current:
            groups.append((transcript_payload(current, timezone), len(current)))

    total = len(groups)
    chunks = tuple(
        ChunkPlan(
            index=index,
            total=total,
            transcript=transcript,
            message_count=count,
            fingerprint=_fingerprint(str(index), str(total), targets_key, transcript),
        )
        for index, (transcript, count) in enumerate(groups)
    )
    final_fingerprint = _fingerprint(
        "final",
        targets_key,
        *(chunk.fingerprint for chunk in chunks),
    )
    return SummaryPlan(
        chunks=chunks,
        final_fingerprint=final_fingerprint,
        transcript_characters=len(full_transcript),
        message_count=len(messages),
    )


@dataclass(frozen=True, slots=True)
class CheckpointEntry:
    fingerprint: str
    payload: dict[str, Any]


@dataclass(slots=True)
class SummaryCheckpoint:
    """Chunk summaries that already succeeded, so they are never paid for twice."""

    chunk_total: int = 0
    chunks: dict[int, CheckpointEntry] = field(default_factory=dict)
    final: CheckpointEntry | None = None
    stage: str | None = None

    @property
    def completed_chunk_count(self) -> int:
        return len(self.chunks)

    def chunk(self, index: int, fingerprint: str) -> dict[str, Any] | None:
        entry = self.chunks.get(index)
        return entry.payload if entry is not None and entry.fingerprint == fingerprint else None

    def final_result(self, fingerprint: str) -> dict[str, Any] | None:
        entry = self.final
        return entry.payload if entry is not None and entry.fingerprint == fingerprint else None

    def remaining_requests(self, plan: SummaryPlan) -> int:
        """How many Gemini requests this plan still needs given what is stored."""
        if self.final_result(plan.final_fingerprint) is not None:
            return 0
        if not plan.uses_chunk_fallback:
            return 1
        pending = sum(
            1 for chunk in plan.chunks if self.chunk(chunk.index, chunk.fingerprint) is None
        )
        return pending + 1


def encode_checkpoint(checkpoint: SummaryCheckpoint) -> str:
    payload: dict[str, Any] = {
        "chunk_total": checkpoint.chunk_total,
        "stage": checkpoint.stage,
        "chunks": [
            {
                "index": index,
                "fingerprint": entry.fingerprint,
                "payload": entry.payload,
            }
            for index, entry in sorted(checkpoint.chunks.items())
        ],
    }
    if checkpoint.final is not None:
        payload["final"] = {
            "fingerprint": checkpoint.final.fingerprint,
            "payload": checkpoint.final.payload,
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_checkpoint(value: str | None) -> SummaryCheckpoint:
    if not value:
        return SummaryCheckpoint()
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return SummaryCheckpoint()
    if not isinstance(payload, dict):
        return SummaryCheckpoint()

    chunks: dict[int, CheckpointEntry] = {}
    for item in payload.get("chunks") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["index"])
            fingerprint = str(item["fingerprint"])
            stored = item["payload"]
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(stored, dict):
            chunks[index] = CheckpointEntry(fingerprint=fingerprint, payload=stored)

    final: CheckpointEntry | None = None
    raw_final = payload.get("final")
    if isinstance(raw_final, dict) and isinstance(raw_final.get("payload"), dict):
        final = CheckpointEntry(
            fingerprint=str(raw_final.get("fingerprint", "")),
            payload=raw_final["payload"],
        )

    stage = payload.get("stage")
    try:
        chunk_total = max(0, int(payload.get("chunk_total", 0)))
    except (TypeError, ValueError):
        chunk_total = 0
    return SummaryCheckpoint(
        chunk_total=chunk_total,
        chunks=chunks,
        final=final,
        stage=str(stage) if isinstance(stage, str) else None,
    )


class InMemorySummarySession:
    """Budget and checkpoint kept in memory, for previews and tests."""

    def __init__(
        self,
        *,
        limit: int = FREE_TIER_RPD,
        request_count: int = 0,
        checkpoint: SummaryCheckpoint | None = None,
        pacer: RequestPacer | None = None,
    ) -> None:
        self.limit = limit
        self.request_count = request_count
        self.checkpoint = checkpoint or SummaryCheckpoint()
        self._pacer = pacer

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.request_count)

    def ensure_budget(self, planned_requests: int) -> None:
        if planned_requests > self.remaining:
            raise AIRequestFailure(AIRetryKind.BUDGET_EXHAUSTED)

    async def reserve(self, estimated_tokens: int) -> None:
        if self.remaining <= 0:
            raise AIRequestFailure(AIRetryKind.BUDGET_EXHAUSTED)
        if self._pacer is not None:
            await self._pacer.acquire(estimated_tokens)
        self.request_count += 1

    def completed_chunk(self, index: int, fingerprint: str) -> dict[str, Any] | None:
        return self.checkpoint.chunk(index, fingerprint)

    async def store_chunk(self, index: int, fingerprint: str, payload: dict[str, Any]) -> None:
        self.checkpoint.chunks[index] = CheckpointEntry(fingerprint=fingerprint, payload=payload)

    def completed_final(self, fingerprint: str) -> dict[str, Any] | None:
        return self.checkpoint.final_result(fingerprint)

    async def store_final(self, fingerprint: str, payload: dict[str, Any]) -> None:
        self.checkpoint.final = CheckpointEntry(fingerprint=fingerprint, payload=payload)

    async def start_plan(self, plan: SummaryPlan) -> None:
        self.checkpoint.chunk_total = plan.chunk_count
        self.checkpoint.stage = "plan"

    async def set_stage(self, stage: str) -> None:
        self.checkpoint.stage = stage


class RequestPacer:
    """Delays requests so a report stays inside the free per-minute ceilings."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        requests_per_minute: int = PACING_REQUESTS_PER_MINUTE,
        tokens_per_minute: int = PACING_TOKENS_PER_MINUTE,
    ) -> None:
        self._sleep = sleep
        self._monotonic = monotonic
        self._requests_per_minute = max(1, requests_per_minute)
        self._tokens_per_minute = max(1, tokens_per_minute)
        self._recent: list[tuple[float, int]] = []

    def _prune(self, now: float) -> None:
        self._recent = [item for item in self._recent if now - item[0] < 60.0]

    async def acquire(self, estimated_tokens: int) -> None:
        tokens = max(0, estimated_tokens)
        # Each pass either exits or retires the oldest request, so the wait is
        # always finite even when the clock does not move.
        while self._recent:
            now = self._monotonic()
            self._prune(now)
            if not self._recent:
                break
            used = sum(item[1] for item in self._recent)
            over_requests = len(self._recent) >= self._requests_per_minute
            over_tokens = used + tokens > self._tokens_per_minute
            if not over_requests and not over_tokens:
                break
            delay = max(0.0, 60.0 - (now - self._recent[0][0]))
            if delay > 0:
                await self._sleep(delay)
            self._recent.pop(0)
        self._recent.append((self._monotonic(), tokens))
