"""性质测试与固定样例共用的结果不变量检查。"""

from __future__ import annotations

import json

from cmdline_parser import AnalyzerOptions
from cmdline_parser.models import KIND_ALIAS, KIND_CASEFOLD, KIND_ORIGINAL, KIND_SUBWORD
from cmdline_parser.normalization import base_term, remove_insertions
from cmdline_parser.scanner import BOUNDARY_SYMBOLS, QUOTES, contains_boundary

_KIND_RANK = {KIND_ORIGINAL: 0, KIND_CASEFOLD: 1, KIND_ALIAS: 2, KIND_SUBWORD: 3}
_RULE_IDS = {"N-BASE-001", "N-CASEFOLD-001", "N-DEOBF-001", "N-SUBWORD-001"}


def check_terms(token) -> None:
    base = base_term(token.raw)
    alias = remove_insertions(base)
    # 别名只删除字符，是基础词的子序列。
    remaining = iter(base)
    assert all(char in remaining for char in alias)
    allowed = {base, base.casefold(), alias, alias.casefold()}
    values = [term.value for term in token.terms]
    assert len(values) == len(set(values))
    ranks = [_KIND_RANK[term.kind] for term in token.terms]
    assert ranks == sorted(ranks)
    for term in token.terms:
        assert term.value
        assert term.rules and set(term.rules) <= _RULE_IDS
        assert len(term.rules) == len(set(term.rules))
        assert isinstance(term.noise, bool)
        if term.kind == KIND_SUBWORD:
            assert any(term.value in source for source in allowed)
        else:
            assert term.value in allowed
    # N-NOISE-001 作用于整个片段：同一片段的词项要么全是噪声，要么全不是；
    # 且噪声片段不再产生子词。
    flags = {term.noise for term in token.terms}
    assert len(flags) <= 1
    if flags == {True}:
        assert all(term.kind != KIND_SUBWORD for term in token.terms)
    if base:
        assert token.terms == [] or token.terms[0].value == base
    else:
        assert token.terms == []


def check_layer(text: str, end: int, tokens, groups, symbols, *, complete: bool) -> None:
    for index, token in enumerate(tokens):
        assert token.position == index
        assert token.raw and text[token.span[0] : token.span[1]] == token.raw
        assert 0 <= token.span[0] < token.span[1] <= end
        assert not contains_boundary(token.raw)
        check_terms(token)
    for symbol in symbols:
        assert symbol.raw and text[symbol.span[0] : symbol.span[1]] == symbol.raw
        assert set(symbol.raw) <= set(BOUNDARY_SYMBOLS)
        assert symbol.span[1] <= end

    # 基础片段与符号互不重叠，按位置排列；两者之间只有空白。
    pos = 0
    for start, stop in sorted([t.span for t in tokens] + [s.span for s in symbols]):
        assert start >= pos
        assert start == pos or text[pos:start].isspace()
        pos = stop
    if complete:
        assert text[pos:end] == "" or text[pos:end].isspace()

    pos = 0
    for group in groups:
        start, stop = group.span
        assert pos <= start < stop <= end
        assert text[start:stop] == group.raw
        assert group.quote in QUOTES and group.raw[0] == group.quote
        c_start, c_stop = group.content_span
        assert text[c_start:c_stop] == group.content
        assert start < c_start <= c_stop <= stop
        if group.closed:
            assert len(group.raw) >= 2 and group.raw[-1] == group.quote
            assert group.quote not in group.content
        else:
            assert stop == end and group.quote not in group.content
        pos = stop


def check_result(text: str, result, options: AnalyzerOptions | None = None) -> None:
    """检查一次分析结果满足协议层面的全部不变量。"""
    options = options or AnalyzerOptions()
    processing = result.processing
    assert result.raw == text
    assert processing.input_length == len(text)
    end = processing.scanned_span[1]
    assert processing.scanned_span[0] == 0 and end == min(len(text), options.max_scan_chars)
    assert processing.scan_complete == (end == len(text))
    limit_names = {item["name"] for item in processing.limits}
    assert processing.limited == bool(processing.limits)
    complete = "max_output_items" not in limit_names
    check_layer(text, end, result.tokens, result.groups, result.symbols, complete=complete)

    views = {}
    total_items = len(result.tokens) + len(result.groups) + len(result.symbols)
    for index, view in enumerate(result.decoded_views):
        assert view.id == f"v{index + 1}"
        if view.parent_view_id is None:
            parent_text, parent_depth = text, 0
        else:
            parent = views[view.parent_view_id]
            parent_text, parent_depth = parent.text, parent.depth
        assert view.depth == parent_depth + 1 <= options.max_decode_depth
        start, stop = view.source_span
        assert 0 <= start < stop <= len(parent_text)
        assert view.status in {"complete", "partial"}
        assert view.mapping == "range"
        assert view.confidence in {"high", "medium", "low"}
        assert all(isinstance(item, str) and item for item in view.evidence)
        # 有证据必然定级 high：分级顺序规定证据优先于形态。
        assert not view.evidence or view.confidence == "high"
        assert view.text
        check_layer(view.text, len(view.text), view.tokens, view.groups, view.symbols, complete=complete)
        total_items += len(view.tokens) + len(view.groups) + len(view.symbols)
        views[view.id] = view

    assert total_items <= options.max_output_items
    assert len(result.decoded_views) <= options.max_decoded_views
    assert processing.stats["decode_attempts"] <= options.max_decode_attempts
    assert processing.stats["decoded_bytes"] <= options.max_decoded_bytes
    ordinary = [n for n in result.notices if n.code != "LIMIT_REACHED"]
    assert len(ordinary) <= options.max_notices
    limit_notices = [n for n in result.notices if n.code == "LIMIT_REACHED"]
    assert [n.details["limit"] for n in limit_notices] == [item["name"] for item in processing.limits]
    assert result.notices[len(ordinary) :] == limit_notices
    for token in result.tokens:
        assert len(token.terms) <= options.max_terms_per_token

    # M-OBFUS-001：度量恒定存在，占比落在 [0,1]，触发项是度量名的子集。
    obfuscation = processing.obfuscation
    assert set(obfuscation) == {
        "symbol_ratio",
        "case_toggles",
        "charcode_coverage",
        "quote_chars",
        "concat_segments",
        "triggered",
    }
    assert 0.0 <= obfuscation["symbol_ratio"] <= 1.0
    assert 0.0 <= obfuscation["charcode_coverage"] <= 1.0
    assert set(obfuscation["triggered"]) <= set(obfuscation) - {"triggered"}
    suspected = [n for n in result.notices if n.code == "OBFUSCATION_SUSPECTED"]
    # 无触发项时一定没有通知；有触发项时至多一条（可能被 max_notices 挡下）。
    assert len(suspected) <= (1 if obfuscation["triggered"] else 0)

    data = result.to_dict()
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data
