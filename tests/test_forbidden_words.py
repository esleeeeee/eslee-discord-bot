import pytest

from eslee_bot.services.moderation_service import (
    find_forbidden_words,
    parse_forbidden_word_batch,
)
from eslee_bot.utils.text import normalize_forbidden_word


def pairs(*words: str) -> list[tuple[str, str]]:
    return [(word, normalize_forbidden_word(word)) for word in words]


def test_substring_match() -> None:
    assert find_forbidden_words("청사과나무", pairs("사과")) == ["사과"]


def test_english_matching_is_case_insensitive() -> None:
    assert find_forbidden_words("This is a Test", pairs("TEST")) == ["TEST"]


def test_unicode_compatibility_normalization() -> None:
    assert find_forbidden_words("ＴＥＳＴ 메시지", pairs("test")) == ["test"]


def test_multiple_words_are_reported_once() -> None:
    words = pairs("사과", "바나나")
    assert find_forbidden_words("사과와 바나나와 사과", words) == ["사과", "바나나"]


def test_duplicate_normalized_entries_are_deduplicated() -> None:
    entries = [("TEST", "test"), ("test", "test")]
    assert find_forbidden_words("test", entries) == ["TEST"]


@pytest.mark.parametrize("invalid", ["", " ", "\t\n"])
def test_blank_forbidden_words_are_rejected(invalid: str) -> None:
    with pytest.raises(ValueError, match="비어 있거나 공백"):
        normalize_forbidden_word(invalid)


def test_overly_long_forbidden_word_is_rejected() -> None:
    with pytest.raises(ValueError, match="100자"):
        normalize_forbidden_word("가" * 101)


def test_normal_message_passes() -> None:
    assert find_forbidden_words("안녕하세요", pairs("사과", "test")) == []


def test_batch_parser_accepts_commas_and_newlines() -> None:
    assert parse_forbidden_word_batch("사과, 바나나\nTEST") == [
        ("사과", "사과"),
        ("바나나", "바나나"),
        ("TEST", "test"),
    ]


def test_batch_parser_removes_casefolded_duplicates() -> None:
    assert parse_forbidden_word_batch("TEST, test, Test") == [("TEST", "test")]


def test_batch_parser_rejects_too_many_words() -> None:
    with pytest.raises(ValueError, match="최대 500개"):
        parse_forbidden_word_batch(",".join(f"w{index}" for index in range(501)))
