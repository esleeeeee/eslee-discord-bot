from __future__ import annotations

import unicodedata

_HANGUL_SYLLABLE_START = 0xAC00
_HANGUL_SYLLABLE_END = 0xD7A3
_HANGUL_JUNGSEONG_COUNT = 21
_HANGUL_JONGSEONG_COUNT = 28
_HANGUL_SYLLABLES_PER_CHOSEONG = _HANGUL_JUNGSEONG_COUNT * _HANGUL_JONGSEONG_COUNT

_COMPAT_CHOSEONG = tuple("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
_COMPAT_JUNGSEONG = tuple("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
_COMPAT_JONGSEONG = tuple("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")

_CANONICAL_JAMO_TO_COMPAT = {
    **{chr(0x1100 + index): value for index, value in enumerate(_COMPAT_CHOSEONG)},
    **{chr(0x1161 + index): value for index, value in enumerate(_COMPAT_JUNGSEONG)},
    **{chr(0x11A8 + index): value for index, value in enumerate(_COMPAT_JONGSEONG)},
}


def normalize_text(value: str) -> str:
    """Normalize compatibility characters and case without removing separators."""
    return unicodedata.normalize("NFKC", value).casefold()


def normalize_for_matching(value: str) -> str:
    """Build a comparison key that also unifies composed and separated Hangul Jamo."""
    normalized = normalize_text(value)
    result: list[str] = []
    for character in normalized:
        codepoint = ord(character)
        if _HANGUL_SYLLABLE_START <= codepoint <= _HANGUL_SYLLABLE_END:
            syllable_index = codepoint - _HANGUL_SYLLABLE_START
            choseong_index, remainder = divmod(
                syllable_index, _HANGUL_SYLLABLES_PER_CHOSEONG
            )
            jungseong_index, jongseong_index = divmod(remainder, _HANGUL_JONGSEONG_COUNT)
            result.append(_COMPAT_CHOSEONG[choseong_index])
            result.append(_COMPAT_JUNGSEONG[jungseong_index])
            if jongseong_index:
                result.append(_COMPAT_JONGSEONG[jongseong_index - 1])
            continue
        result.append(_CANONICAL_JAMO_TO_COMPAT.get(character, character))
    return "".join(result)


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
