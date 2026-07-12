from __future__ import annotations

import unicodedata


def normalize_text(value: str) -> str:
    """Normalize compatibility characters and case without removing separators."""
    return unicodedata.normalize("NFKC", value).casefold()


def normalize_forbidden_word(value: str, *, max_length: int = 100) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("금지어는 비어 있거나 공백으로만 구성될 수 없습니다.")
    if len(stripped) > max_length:
        raise ValueError(f"금지어는 {max_length}자 이하여야 합니다.")
    return normalize_text(stripped)


def truncate_text(value: str, limit: int, *, suffix: str = "…") -> str:
    if limit < len(suffix):
        raise ValueError("limit must be at least the suffix length")
    if len(value) <= limit:
        return value
    return value[: limit - len(suffix)].rstrip() + suffix
