from __future__ import annotations

import pytest

from cmdline_parser import analyze
from cmdline_parser.models import KIND_ALIAS, KIND_CASEFOLD, KIND_ORIGINAL, KIND_SUBWORD
from cmdline_parser.normalization import (
    RULE_BASE,
    RULE_CASEFOLD,
    RULE_DEOBF,
    RULE_SUBWORD,
    base_term,
    build_terms,
    edge_quote_widths,
    extract_subwords,
    remove_insertions,
)

_KIND_RANK = {KIND_ORIGINAL: 0, KIND_CASEFOLD: 1, KIND_ALIAS: 2, KIND_SUBWORD: 3}


def _values(raw: str, **kwargs) -> list[str]:
    return [term.value for term in build_terms(raw, **kwargs)[0]]


def _term(raw: str, value: str):
    for term in build_terms(raw)[0]:
        if term.value == value:
            return term
    raise AssertionError(f"{value!r} not in terms of {raw!r}")


def test_mixed_case_token_order_and_rules() -> None:
    terms, limited = build_terms("NetStat.EXE")
    assert not limited
    assert [(t.value, t.kind, t.rules) for t in terms] == [
        ("NetStat.EXE", KIND_ORIGINAL, [RULE_BASE]),
        ("netstat.exe", KIND_CASEFOLD, [RULE_CASEFOLD]),
        ("NetStat", KIND_SUBWORD, [RULE_SUBWORD]),
        ("EXE", KIND_SUBWORD, [RULE_SUBWORD]),
        ("netstat", KIND_SUBWORD, [RULE_CASEFOLD, RULE_SUBWORD]),
        ("exe", KIND_SUBWORD, [RULE_CASEFOLD, RULE_SUBWORD]),
    ]


def test_same_value_merges_and_keeps_first_kind() -> None:
    terms, _ = build_terms("hello")
    assert [(t.value, t.kind, t.rules) for t in terms] == [
        ("hello", KIND_ORIGINAL, [RULE_BASE, RULE_CASEFOLD, RULE_SUBWORD]),
    ]


@pytest.mark.parametrize(
    ("raw", "alias"),
    [
        ("ne^tstat.exe", "netstat.exe"),
        ("fi^ndstr", "findstr"),
        ('po"wer"shell.exe', "powershell.exe"),
        ("po`wer`shell", "powershell"),
        ("w'h'o'a'm'i", "whoami"),
        ("ne^^^tstat", "netstat"),
        ("c^m^d.exe", "cmd.exe"),
    ],
)
def test_insertion_aliases(raw: str, alias: str) -> None:
    term = _term(raw, alias)
    assert term.kind == KIND_ALIAS
    assert term.rules[0] == RULE_DEOBF
    assert build_terms(raw)[0][0].value == raw


def test_alias_is_also_casefolded() -> None:
    term = _term("Po^werShell", "powershell")
    assert term.kind == KIND_ALIAS
    # 别名本身也是一个完整子词，同值合并后追加 N-SUBWORD-001。
    assert term.rules == [RULE_DEOBF, RULE_CASEFOLD, RULE_SUBWORD]
    assert _term("Po^werShell", "PowerShell").rules == [RULE_DEOBF, RULE_SUBWORD]
    terms, _ = build_terms("Po^werShell", subwords=False)
    assert [(t.value, t.rules) for t in terms if t.kind == KIND_ALIAS] == [
        ("PowerShell", [RULE_DEOBF]),
        ("powershell", [RULE_DEOBF, RULE_CASEFOLD]),
    ]


@pytest.mark.parametrize("raw", ["a^", "^", "^a", "a^.b", "x-^y", "^^", "a^ "])
def test_insertions_need_word_chars_on_both_sides(raw: str) -> None:
    assert remove_insertions(raw) == raw
    assert all(t.kind != KIND_ALIAS for t in build_terms(raw)[0])


def test_edge_quotes_are_stripped_from_base_term_only() -> None:
    assert base_term('"netstat"') == "netstat"
    assert base_term("''a''") == "a"
    assert base_term('"\'"') == ""
    assert edge_quote_widths('"a"b"') == (1, 1)
    assert edge_quote_widths('"""') == (3, 0)
    assert _values('"Tool.exe"')[:2] == ["Tool.exe", "tool.exe"]


def test_empty_base_term_produces_no_terms() -> None:
    assert build_terms('""') == ([], False)
    assert build_terms("'") == ([], False)


def test_backslashes_are_not_removed() -> None:
    values = _values(r"C:\Program")
    assert values[0] == r"C:\Program"
    assert "C" in values and "Program" in values
    assert "C:Program" not in values and "CProgram" not in values


def test_long_option_subwords() -> None:
    values = _values("--name=value")
    assert values[0] == "--name=value"
    assert "name" in values and "value" in values


def test_url_and_ipv6_subwords() -> None:
    values = _values("https://example.com/a?b=1")
    assert {"https", "example", "com", "a", "b", "1"} <= set(values)
    values = _values("[fe80::1]")
    assert "fe80" in values and "1" in values


def test_fullwidth_letters_are_not_replaced_with_ascii() -> None:
    values = _values("Ｗｒｉｔｅ")
    assert values == ["Ｗｒｉｔｅ", "ｗｒｉｔｅ"]
    assert "write" not in values and "Write" not in values


def test_cjk_and_emoji() -> None:
    assert _values("你好") == ["你好"]
    assert _values("😀") == ["😀"]
    assert _values("a😀b") == ["a😀b", "a", "b"]


def test_combining_marks_attach_to_previous_word() -> None:
    assert extract_subwords("cafe\u0301-bar") == ["cafe\u0301", "bar"]
    assert extract_subwords("\u0301abc") == ["abc"]


def test_casefold_is_not_lower() -> None:
    values = _values("STRASSE-Straße")
    assert "strasse-strasse" in values
    assert "Straße" in values


def test_subwords_can_be_disabled() -> None:
    terms, _ = build_terms("Ne^tStat.exe", subwords=False)
    assert all(t.kind != KIND_SUBWORD for t in terms)
    assert [t.value for t in terms] == ["Ne^tStat.exe", "ne^tstat.exe", "NetStat.exe", "netstat.exe"]


def test_max_terms_truncates_in_order() -> None:
    terms, limited = build_terms("Aa-Bb-Cc-Dd", max_terms=3)
    assert limited
    assert [t.value for t in terms] == ["Aa-Bb-Cc-Dd", "aa-bb-cc-dd", "Aa"]


def test_max_terms_zero() -> None:
    assert build_terms("abc", max_terms=0) == ([], True)


def test_kind_order_is_fixed() -> None:
    for raw in ["NE^TSTAT.exe", 'P"o"werShell', "--Name=Value", "abc", "C:\\X\\Y"]:
        ranks = [_KIND_RANK[t.kind] for t in build_terms(raw)[0]]
        assert ranks == sorted(ranks), raw


def test_aliases_share_token_position() -> None:
    result = analyze("ne^tstat -ano")
    assert [t.position for t in result.tokens] == [0, 1]
    assert "netstat" in [term.value for term in result.tokens[0].terms]


def test_escaped_pipe_is_not_a_confirmed_pipe() -> None:
    result = analyze("echo a^|b")
    assert [t.raw for t in result.tokens] == ["echo", "a^", "b"]
    assert [s.raw for s in result.symbols] == ["|"]
    assert [t.value for t in result.tokens[1].terms] == ["a^", "a"]


def test_trailing_caret() -> None:
    result = analyze("dir ^")
    assert [t.raw for t in result.tokens] == ["dir", "^"]
    assert [t.value for t in result.tokens[1].terms] == ["^"]
    assert result.symbols == []
