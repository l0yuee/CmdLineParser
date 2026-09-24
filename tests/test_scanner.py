from __future__ import annotations

import re

import pytest

from cmdline_parser import analyze, scanner


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(t.raw, *t.span) for t in analyze(text).tokens]


def _symbols(text: str) -> list[tuple[str, int, int]]:
    return [(s.raw, *s.span) for s in analyze(text).symbols]


def _groups(text: str) -> list[tuple[str, str, bool]]:
    return [(g.raw, g.content, g.closed) for g in analyze(text).groups]


def test_plain_command_spans_and_positions() -> None:
    result = analyze("netstat.exe -ano")
    assert [(t.raw, t.span, t.position) for t in result.tokens] == [
        ("netstat.exe", (0, 11), 0),
        ("-ano", (12, 16), 1),
    ]
    assert result.symbols == []
    assert result.groups == []


@pytest.mark.parametrize("text", ["", " ", "\t\r\n", "\u3000\u00a0 "])
def test_empty_and_whitespace_only(text: str) -> None:
    result = analyze(text)
    assert result.raw == text
    assert result.tokens == [] and result.symbols == [] and result.groups == []
    assert result.decoded_views == [] and result.notices == []
    assert result.processing.scan_complete is True
    assert result.processing.limited is False


def test_symbol_runs_are_single_symbols() -> None:
    assert _symbols("a||b && c;d") == [("||", 1, 3), ("&&", 5, 7), (";", 9, 10)]
    assert _tokens("a||b && c;d") == [("a", 0, 1), ("b", 3, 4), ("c", 8, 9), ("d", 10, 11)]


def test_redirection_is_split_without_semantics() -> None:
    text = "cmd >>out.txt 2>&1"
    assert _tokens(text) == [("cmd", 0, 3), ("out.txt", 6, 13), ("2", 14, 15), ("1", 17, 18)]
    assert _symbols(text) == [(">>", 4, 6), (">&", 15, 17)]
    for symbol in analyze(text).symbols:
        assert set(symbol.to_dict()) == {"raw", "span"}


def test_brackets_and_braces_are_boundaries() -> None:
    assert _tokens("(a)[b]{c}") == [("a", 1, 2), ("b", 4, 5), ("c", 7, 8)]
    assert [s.raw for s in analyze("(a)[b]{c}").symbols] == ["(", ")[", "]{", "}"]


def test_punctuation_stays_inside_tokens() -> None:
    text = r"C:\a/b:c-d_e=f%g+h."
    assert _tokens(text) == [(text, 0, len(text))]


def test_quotes_do_not_stop_whitespace_split() -> None:
    text = 'tool.exe --output "C:\\Program Files\\Exam'
    assert [raw for raw, _, _ in _tokens(text)] == [
        "tool.exe",
        "--output",
        '"C:\\Program',
        "Files\\Exam",
    ]
    assert _groups(text) == [('"C:\\Program Files\\Exam', "C:\\Program Files\\Exam", False)]


def test_unicode_whitespace_is_a_boundary() -> None:
    assert [raw for raw, _, _ in _tokens("a\u3000b\u00a0c\u2003d")] == ["a", "b", "c", "d"]


def test_regex_whitespace_matches_str_isspace() -> None:
    pattern = re.compile(r"\s")
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        char = chr(code)
        assert bool(pattern.match(char)) == char.isspace(), hex(code)


def test_groups_pair_same_quotes_and_keep_other_quotes_as_content() -> None:
    assert _groups("echo \"it's\" 'say \"hi\"'") == [
        ("\"it's\"", "it's", True),
        ("'say \"hi\"'", 'say "hi"', True),
    ]


def test_stray_quote_after_closed_group() -> None:
    text = "echo 'a \"b' c\""
    assert _groups(text) == [("'a \"b'", 'a "b', True), ('"', "", False)]
    result = analyze(text)
    assert [n.code for n in result.notices] == ["UNCLOSED_QUOTE"]
    assert result.notices[0].span == (13, 14)


def test_empty_quotes_keep_tokens_and_groups_without_empty_terms() -> None:
    result = analyze("tool \"\" ''")
    assert [t.raw for t in result.tokens] == ["tool", '""', "''"]
    assert result.tokens[1].terms == [] and result.tokens[2].terms == []
    assert [(g.raw, g.content, g.closed) for g in result.groups] == [
        ('""', "", True),
        ("''", "", True),
    ]


def test_group_content_span() -> None:
    text = 'a "bc" "de'
    result = analyze(text)
    for group in result.groups:
        assert text[group.span[0] : group.span[1]] == group.raw
        assert text[group.content_span[0] : group.content_span[1]] == group.content
    assert [g.content_span for g in result.groups] == [(3, 5), (8, 10)]


def test_iter_segments_respects_end() -> None:
    assert list(scanner.iter_segments("abc def", 5)) == [
        (scanner.TOKEN, 0, 3),
        (scanner.TOKEN, 4, 5),
    ]


def test_iter_groups_respects_end() -> None:
    assert list(scanner.iter_groups('"ab" "cd"', 7)) == [('"', 0, 4, True), ('"', 5, 7, False)]


def test_contains_boundary() -> None:
    assert scanner.contains_boundary("a b")
    assert scanner.contains_boundary("a|b")
    assert not scanner.contains_boundary("a-b.c")
