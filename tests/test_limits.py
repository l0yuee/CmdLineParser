"""资源上限：达到上限时不报错、保留已完成结果，并给出不会被隐藏的受限记录。"""

from __future__ import annotations

import base64

import pytest

from cmdline_parser import AnalyzerOptions, analyze

PAYLOAD = "Write-Output hello"
ENCODED = base64.b64encode(PAYLOAD.encode()).decode("ascii")


def _limit_names(result) -> list[str]:
    return [item["name"] for item in result.processing.limits]


def _check_limit_notice(result, name: str) -> None:
    notices = [n for n in result.notices if n.code == "LIMIT_REACHED"]
    assert name in [n.details["limit"] for n in notices]
    for notice in notices:
        assert notice.level == "limit"


def test_defaults_are_not_limited() -> None:
    result = analyze(f"tool {ENCODED}")
    assert not result.processing.limited
    assert result.processing.limits == []


def test_max_scan_chars() -> None:
    text = "netstat -ano"
    assert not analyze(text, options=AnalyzerOptions(max_scan_chars=12)).processing.limited
    result = analyze(text, options=AnalyzerOptions(max_scan_chars=5))
    assert result.raw == text
    assert [t.raw for t in result.tokens] == ["netst"]
    assert result.processing.scanned_span == (0, 5)
    assert result.processing.scan_complete is False
    assert result.processing.input_length == 12
    assert _limit_names(result) == ["max_scan_chars"]
    _check_limit_notice(result, "max_scan_chars")
    assert [n.span for n in result.notices] == [(5, 12)]


def test_max_scan_chars_zero() -> None:
    result = analyze("abc", options=AnalyzerOptions(max_scan_chars=0))
    assert result.tokens == [] and result.raw == "abc"
    assert _limit_names(result) == ["max_scan_chars"]


def test_unclosed_group_stops_at_scan_end() -> None:
    result = analyze('a "bcdef', options=AnalyzerOptions(max_scan_chars=5))
    assert [(g.raw, g.closed) for g in result.groups] == [('"bc', False)]


def test_max_output_items() -> None:
    text = "a b|c"
    assert not analyze(text, options=AnalyzerOptions(max_output_items=4)).processing.limited
    result = analyze(text, options=AnalyzerOptions(max_output_items=3))
    assert [t.raw for t in result.tokens] == ["a", "b"]
    assert [s.raw for s in result.symbols] == ["|"]
    assert _limit_names(result) == ["max_output_items"]
    assert result.processing.stats["output_items"] == 3


def test_max_output_items_is_shared_with_views() -> None:
    text = f"tool {ENCODED}"
    result = analyze(text, options=AnalyzerOptions(max_output_items=3))
    assert len(result.decoded_views) == 1
    assert [t.raw for t in result.decoded_views[0].tokens] == ["Write-Output"]
    assert _limit_names(result) == ["max_output_items"]
    assert result.processing.limits[0]["count"] == 1


def test_max_terms_per_token() -> None:
    assert not analyze("NetStat", options=AnalyzerOptions(max_terms_per_token=2)).processing.limited
    result = analyze("NetStat", options=AnalyzerOptions(max_terms_per_token=1))
    assert [t.value for t in result.tokens[0].terms] == ["NetStat"]
    assert _limit_names(result) == ["max_terms_per_token"]


def test_max_notices_does_not_hide_limit_notices() -> None:
    text = 'a "b'
    assert not analyze(text, options=AnalyzerOptions(max_notices=1)).processing.limited
    result = analyze(f"{ENCODED} {text}", options=AnalyzerOptions(max_notices=0, max_decode_attempts=0))
    assert result.groups[0].closed is False
    assert [n.code for n in result.notices] == ["LIMIT_REACHED", "LIMIT_REACHED"]
    assert _limit_names(result) == ["max_notices", "max_decode_attempts"]


def test_max_notices_zero_with_unclosed_quote() -> None:
    result = analyze('a "b', options=AnalyzerOptions(max_notices=0))
    assert [(n.code, n.details["limit"]) for n in result.notices] == [("LIMIT_REACHED", "max_notices")]


def test_max_decode_attempts() -> None:
    text = f"tool {ENCODED}"
    assert len(analyze(text, options=AnalyzerOptions(max_decode_attempts=1)).decoded_views) == 1
    result = analyze(text, options=AnalyzerOptions(max_decode_attempts=0))
    assert result.decoded_views == []
    assert _limit_names(result) == ["max_decode_attempts"]
    _check_limit_notice(result, "max_decode_attempts")


def test_max_candidate_chars() -> None:
    text = f"tool {ENCODED}"
    size = len(ENCODED)
    assert len(analyze(text, options=AnalyzerOptions(max_candidate_chars=size)).decoded_views) == 1
    result = analyze(text, options=AnalyzerOptions(max_candidate_chars=size - 1))
    assert result.decoded_views == []
    assert _limit_names(result) == ["max_candidate_chars"]
    assert result.processing.stats["decode_attempts"] == 0


def test_max_decoded_views() -> None:
    text = f"tool {ENCODED}"
    assert len(analyze(text, options=AnalyzerOptions(max_decoded_views=1)).decoded_views) == 1
    result = analyze(text, options=AnalyzerOptions(max_decoded_views=0))
    assert result.decoded_views == []
    assert _limit_names(result) == ["max_decoded_views"]


def test_max_decoded_bytes() -> None:
    text = f"tool {ENCODED}"
    size = len(PAYLOAD.encode())
    ok = analyze(text, options=AnalyzerOptions(max_decoded_bytes=size))
    assert len(ok.decoded_views) == 1
    assert ok.processing.stats["decoded_bytes"] == size
    result = analyze(text, options=AnalyzerOptions(max_decoded_bytes=size - 1))
    assert result.decoded_views == []
    assert _limit_names(result) == ["max_decoded_bytes"]


def test_max_decode_depth_zero() -> None:
    result = analyze(f"tool {ENCODED}", options=AnalyzerOptions(max_decode_depth=0))
    assert result.decoded_views == []
    assert _limit_names(result) == ["max_decode_depth"]


def test_decode_disabled_is_not_a_limit() -> None:
    result = analyze(f"tool {ENCODED}", options=AnalyzerOptions(decode=False))
    assert result.decoded_views == []
    assert result.processing.decode_enabled is False
    assert result.processing.limited is False


def test_subwords_disabled() -> None:
    result = analyze("Net-Stat", options=AnalyzerOptions(subwords=False))
    assert [t.value for t in result.tokens[0].terms] == ["Net-Stat", "net-stat"]


def test_many_limits_are_aggregated_per_name() -> None:
    text = " ".join(["x"] * 10)
    result = analyze(text, options=AnalyzerOptions(max_output_items=2))
    assert result.processing.limits == [{"name": "max_output_items", "limit": 2, "count": 1}]


def test_large_input_is_bounded() -> None:
    text = "a " * 600_000
    result = analyze(text)
    assert result.raw == text
    assert result.processing.scanned_span == (0, 1_048_576)
    assert len(result.tokens) == 100_000
    assert set(_limit_names(result)) == {"max_scan_chars", "max_output_items"}


def test_many_candidates_exhaust_attempts_without_failing() -> None:
    parts = [base64.b64encode(f"Write-Output item{i:03d}".encode()).decode() for i in range(80)]
    result = analyze(" ".join(parts))
    assert len(result.decoded_views) == 16
    assert result.processing.stats["decode_attempts"] <= 64
    assert "max_decoded_views" in _limit_names(result)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_scan_chars": -1}, ValueError),
        ({"max_scan_chars": 1.5}, TypeError),
        ({"max_scan_chars": True}, TypeError),
        ({"decode": 1}, TypeError),
        ({"subwords": None}, TypeError),
    ],
)
def test_option_validation(kwargs, error) -> None:
    with pytest.raises(error):
        AnalyzerOptions(**kwargs)


def test_options_are_keyword_only_and_frozen() -> None:
    with pytest.raises(TypeError):
        AnalyzerOptions(False)  # type: ignore[misc]
    options = AnalyzerOptions()
    with pytest.raises(AttributeError):
        options.decode = False  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, b"netstat", 1, ["a"]])
def test_non_str_input_is_a_caller_error(value) -> None:
    with pytest.raises(TypeError):
        analyze(value)  # type: ignore[arg-type]


def test_invalid_options_type() -> None:
    with pytest.raises(TypeError):
        analyze("a", options={"decode": False})  # type: ignore[arg-type]
