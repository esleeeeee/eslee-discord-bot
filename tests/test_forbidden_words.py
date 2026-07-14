import pytest

from eslee_bot.services.moderation_service import (
    MAX_OBFUSCATION_GAP,
    find_forbidden_words,
    parse_forbidden_word_batch,
)
from eslee_bot.utils.text import normalize_for_matching, normalize_forbidden_word


def pairs(*words: str) -> list[tuple[str, str]]:
    return [(word, normalize_forbidden_word(word)) for word in words]


def test_substring_match() -> None:
    assert find_forbidden_words("청사과나무", pairs("사과")) == ["사과"]


def test_english_matching_is_case_insensitive() -> None:
    assert find_forbidden_words("This is a Test", pairs("TEST")) == ["TEST"]


def test_unicode_compatibility_normalization() -> None:
    assert find_forbidden_words("ＴＥＳＴ 메시지", pairs("test")) == ["test"]


@pytest.mark.parametrize(
    "message",
    [
        "주식",
        "해외주식",
        "주식시장",
        "주.식",
        "주`식",
        "주~식",
        "주1식",
        "주123식",
        "주@식",
        "주 식",
        "주_식",
        "주-식",
        "주!식",
        "주___식",
        "주ㅋㅋ식",
        "주ㅋㅋㅋㅋ식",
        "주ㅎㅎ식",
    ],
)
def test_common_obfuscated_korean_forms_are_detected(message: str) -> None:
    assert find_forbidden_words(message, pairs("주식")) == ["주식"]


@pytest.mark.parametrize("separator", ["  ", "\t", "\n", "\u2003", "\u3000"])
def test_unicode_whitespace_obfuscation_is_detected(separator: str) -> None:
    assert find_forbidden_words(f"주{separator}식", pairs("주식")) == ["주식"]


@pytest.mark.parametrize("separator", ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"])
def test_invisible_unicode_obfuscation_is_detected(separator: str) -> None:
    assert find_forbidden_words(f"주{separator}식", pairs("주식")) == ["주식"]


@pytest.mark.parametrize("message", ["ㅈㅜㅅㅣㄱ", "주ㅅㅣㄱ"])
def test_separated_hangul_jamo_obfuscation_is_detected(message: str) -> None:
    assert find_forbidden_words(message, pairs("주식")) == ["주식"]


def test_matching_normalization_unifies_composed_and_separated_hangul() -> None:
    assert normalize_for_matching("주식") == normalize_for_matching("ㅈㅜㅅㅣㄱ")


@pytest.mark.parametrize(
    "message",
    [
        "주말에 맛있는 식당에 갔다",
        "주인장이 식사를 준비했다",
        "이번 주에는 식당 예약을 했다",
        "주말 저녁에 친구들과 식사를 했다",
        "주아식",
    ],
)
def test_distant_or_arbitrary_korean_text_does_not_match(message: str) -> None:
    assert find_forbidden_words(message, pairs("주식")) == []


def test_obfuscation_gap_is_bounded() -> None:
    assert find_forbidden_words(f"주{'_' * MAX_OBFUSCATION_GAP}식", pairs("주식")) == [
        "주식"
    ]
    assert find_forbidden_words(
        f"주{'_' * (MAX_OBFUSCATION_GAP + 1)}식", pairs("주식")
    ) == []


@pytest.mark.parametrize(
    "message",
    ["비트코인", "비.트.코.인", "비@트@코@인", "비1트2코3인", "비 트 코 인"],
)
def test_multi_character_korean_words_support_bounded_obfuscation(message: str) -> None:
    assert find_forbidden_words(message, pairs("비트코인")) == ["비트코인"]


@pytest.mark.parametrize("message", ["stock", "STOCK", "StOcK", "s.t.o.c.k"])
def test_obfuscated_english_matching_remains_case_insensitive(message: str) -> None:
    assert find_forbidden_words(message, pairs("stock")) == ["stock"]


@pytest.mark.parametrize("message", ["web3", "W.E.B.3", "web_3"])
def test_forbidden_words_containing_digits_keep_existing_semantics(message: str) -> None:
    assert find_forbidden_words(message, pairs("web3")) == ["web3"]


def test_obfuscated_match_returns_original_registered_word() -> None:
    assert find_forbidden_words("주@식 하지마라", pairs("주식")) == ["주식"]


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
