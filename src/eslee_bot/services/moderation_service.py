from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache

from eslee_bot.utils.text import normalize_for_matching, normalize_forbidden_word, truncate_text

MAX_BATCH_WORDS = 500
MAX_OBFUSCATION_GAP = 8
_SHORT_REACTION_FILLERS = frozenset(normalize_for_matching("ㅋㅎ"))


def parse_forbidden_word_batch(
    value: str, *, max_items: int = MAX_BATCH_WORDS
) -> list[tuple[str, str]]:
    """Parse comma/newline-separated words and remove normalized duplicates."""
    candidates = [part.strip() for part in re.split(r"[,\r\n]+", value) if part.strip()]
    if not candidates:
        raise ValueError("금지어를 한 개 이상 입력해 주세요.")
    if len(candidates) > max_items:
        raise ValueError(f"한 번에 최대 {max_items}개까지 등록할 수 있습니다.")

    parsed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for word in candidates:
        normalized = normalize_forbidden_word(word)
        if normalized in seen:
            continue
        seen.add(normalized)
        parsed.append((word, normalized))
    return parsed


def find_forbidden_words(message: str, forbidden_words: Iterable[tuple[str, str]]) -> list[str]:
    """Return every matching display word once, preserving repository order."""
    normalized_message = normalize_for_matching(message)
    matches: list[str] = []
    seen: set[str] = set()
    for display_word, normalized_word in forbidden_words:
        match_key = _forbidden_word_match_key(normalized_word)
        is_new_match = (
            match_key
            and match_key not in seen
            and _contains_forbidden_word(normalized_message, match_key)
        )
        if is_new_match:
            matches.append(display_word)
            seen.add(match_key)
    return matches


@lru_cache(maxsize=4096)
def _forbidden_word_match_key(normalized_word: str) -> str:
    return normalize_for_matching(normalized_word)


def _contains_forbidden_word(message: str, forbidden_word: str) -> bool:
    if forbidden_word in message:
        return True
    if len(forbidden_word) < 2:
        return False

    search_from = 0
    while True:
        start = message.find(forbidden_word[0], search_from)
        if start < 0:
            return False
        cursor = start + 1
        matched = True
        for expected in forbidden_word[1:]:
            if cursor < len(message) and message[cursor] == expected:
                cursor += 1
                continue

            gap_length = 0
            while (
                cursor < len(message)
                and gap_length < MAX_OBFUSCATION_GAP
                and message[cursor] != expected
                and _is_obfuscation_filler(message[cursor])
            ):
                cursor += 1
                gap_length += 1
            if cursor >= len(message) or message[cursor] != expected:
                matched = False
                break
            cursor += 1

        if matched:
            return True
        search_from = start + 1


def _is_obfuscation_filler(character: str) -> bool:
    if character in _SHORT_REACTION_FILLERS or character.isspace():
        return True
    category = unicodedata.category(character)
    return category[0] in {"M", "P", "S"} or category in {"Cc", "Cf", "Nd"}


def build_user_warning(matched_words: Iterable[str]) -> str:
    words = truncate_text(", ".join(f"`{word.replace('`', 'ˋ')}`" for word in matched_words), 1500)
    return (
        "🚫 메시지가 삭제되었습니다.\n\n"
        f"입력한 메시지에서 다음 금지어가 감지되었습니다:\n{words}\n\n"
        "해당 단어가 포함된 메시지는 이 서버에서 전송할 수 없습니다."
    )
