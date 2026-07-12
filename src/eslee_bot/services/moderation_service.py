from __future__ import annotations

import re
from collections.abc import Iterable

from eslee_bot.utils.text import normalize_forbidden_word, normalize_text, truncate_text

MAX_BATCH_WORDS = 500


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
    normalized_message = normalize_text(message)
    matches: list[str] = []
    seen: set[str] = set()
    for display_word, normalized_word in forbidden_words:
        is_new_match = (
            normalized_word
            and normalized_word in normalized_message
            and normalized_word not in seen
        )
        if is_new_match:
            matches.append(display_word)
            seen.add(normalized_word)
    return matches


def build_user_warning(matched_words: Iterable[str]) -> str:
    words = truncate_text(", ".join(f"`{word.replace('`', 'ˋ')}`" for word in matched_words), 1500)
    return (
        "🚫 메시지가 삭제되었습니다.\n\n"
        f"입력한 메시지에서 다음 금지어가 감지되었습니다:\n{words}\n\n"
        "해당 단어가 포함된 메시지는 이 서버에서 전송할 수 없습니다."
    )
