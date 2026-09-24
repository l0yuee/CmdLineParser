"""来源追溯：原始词、别名与解码词都能回到原文区间与规则。"""

from __future__ import annotations

import base64

from cmdline_parser import AnalyzerOptions, analyze

PAYLOAD = "Write-Output hello"


def _b64(text: str, charset: str = "utf-8") -> str:
    return base64.b64encode(text.encode(charset)).decode("ascii")


def _terms(tokens) -> set[str]:
    return {term.value for token in tokens for term in token.terms}


def test_encoded_command_hint() -> None:
    payload = _b64(PAYLOAD, "utf-16le")
    text = f"powershell.exe -NoProfile -EncodedCommand {payload}"
    result = analyze(text)
    assert len(result.decoded_views) == 1
    view = result.decoded_views[0]
    start = text.index(payload)
    assert view.id == "v1" and view.parent_view_id is None and view.depth == 1
    assert view.source_span == (start, start + len(payload))
    assert text[view.source_span[0] : view.source_span[1]] == payload
    assert (view.source_kind, view.candidate_rule) == ("token", "C-TOKEN-001")
    assert (view.rule, view.encoding, view.charset) == ("D-B64-001", "base64", "utf-16le")
    assert view.hint == "C-HINT-ENC-001"
    assert view.status == "complete" and view.mapping == "range"
    assert view.text == PAYLOAD
    assert [t.raw for t in view.tokens] == ["Write-Output", "hello"]
    assert view.tokens[0].span == (0, 12)
    assert {"write-output", "write", "output"} <= _terms(view.tokens)


def test_hint_works_through_casefold_and_obfuscation() -> None:
    payload = _b64("dir", "utf-16le")
    for flag in ["-enc", "-ENC", "-e^nc", "-EnCodedCommand"]:
        result = analyze(f"powershell {flag} {payload}")
        assert [v.text for v in result.decoded_views] == ["dir"], flag
        assert result.decoded_views[0].hint == "C-HINT-ENC-001"


def test_short_payload_without_hint_is_not_decoded() -> None:
    payload = _b64("dir", "utf-16le")
    assert analyze(f"powershell -Command {payload}").decoded_views == []
    assert analyze(f"powershell -e {payload}").decoded_views == []


def test_hint_is_only_the_next_token() -> None:
    payload = _b64("dir", "utf-16le")
    assert analyze(f"powershell -enc x {payload}").decoded_views == []


def test_quoted_token_candidate_span_excludes_quotes() -> None:
    payload = _b64(PAYLOAD)
    text = f"tool '{payload}'"
    view = analyze(text).decoded_views[0]
    assert view.source_kind == "token"
    assert text[view.source_span[0] : view.source_span[1]] == payload


def test_kv_value_candidate() -> None:
    payload = _b64(PAYLOAD)
    text = f'tool --data="{payload}"'
    result = analyze(text)
    assert len(result.decoded_views) == 1
    view = result.decoded_views[0]
    assert (view.source_kind, view.candidate_rule) == ("kv_value", "C-KV-001")
    assert text[view.source_span[0] : view.source_span[1]] == payload
    assert view.text == PAYLOAD


def test_group_candidate_for_in_place_decoder() -> None:
    text = 'echo "Write%2DOutput hello%21"'
    result = analyze(text)
    by_kind = {v.source_kind: v for v in result.decoded_views}
    group_view = by_kind["group"]
    assert group_view.candidate_rule == "C-GROUP-001"
    assert group_view.text == "Write-Output hello!"
    assert text[group_view.source_span[0] : group_view.source_span[1]] == "Write%2DOutput hello%21"
    token_texts = [v.text for v in result.decoded_views if v.source_kind == "token"]
    assert token_texts == ["Write-Output", "hello!"]


def test_nested_percent_then_base64() -> None:
    inner = _b64("Write-Output hello!")
    assert inner.endswith("==")
    wrapped = inner.replace("=", "%3D")
    text = f"run {wrapped}"
    result = analyze(text)
    assert [(v.id, v.parent_view_id, v.depth, v.encoding) for v in result.decoded_views] == [
        ("v1", None, 1, "percent"),
        ("v2", "v1", 2, "base64"),
    ]
    v1, v2 = result.decoded_views
    assert v1.text == inner
    assert text[v1.source_span[0] : v1.source_span[1]] == wrapped
    assert v1.text[v2.source_span[0] : v2.source_span[1]] == inner
    assert v2.text == "Write-Output hello!"
    assert "hello" in _terms(v2.tokens)


def test_nested_base64_then_percent() -> None:
    inner = "run%20Write%2DOutput%20hello"
    payload = _b64(inner)
    result = analyze(f"x {payload}")
    assert [(v.parent_view_id, v.encoding, v.text) for v in result.decoded_views] == [
        (None, "base64", inner),
        ("v1", "percent", "run Write-Output hello"),
    ]


def test_depth_limit_stops_nesting_and_is_reported() -> None:
    inner = _b64("Write-Output hello!")
    text = "run " + inner.replace("=", "%3D")
    result = analyze(text, options=AnalyzerOptions(max_decode_depth=1))
    assert [v.encoding for v in result.decoded_views] == ["percent"]
    assert result.processing.limited
    assert {"name": "max_decode_depth", "limit": 1, "count": 1} in result.processing.limits
    limit_notices = [n for n in result.notices if n.code == "LIMIT_REACHED"]
    assert limit_notices[0].view_id == "v1"


def test_partial_view_notice_points_to_view() -> None:
    payload = _b64(PAYLOAD)[:-3]
    result = analyze(f"x {payload}")
    view = result.decoded_views[0]
    assert view.status == "partial"
    partial = [n for n in result.notices if n.code == "DECODE_PARTIAL"]
    assert len(partial) == 1
    assert partial[0].details["decoded_view_id"] == view.id
    assert partial[0].span == view.source_span
    assert partial[0].view_id is None


def test_decode_failure_is_local_to_candidate() -> None:
    text = "tool ab%FFcd%41 netstat"
    result = analyze(text)
    assert [t.raw for t in result.tokens] == ["tool", "ab%FFcd%41", "netstat"]
    assert result.decoded_views == []
    failed = [n for n in result.notices if n.code == "DECODE_FAILED"]
    assert len(failed) == 1
    assert failed[0].level == "warning"
    assert failed[0].details == {"rule": "D-PCT-001", "reason": "invalid_utf8"}
    assert text[failed[0].span[0] : failed[0].span[1]] == "ab%FFcd%41"


def test_identical_candidates_decode_once() -> None:
    payload = _b64(PAYLOAD)
    result = analyze(f"a {payload} b {payload} '{payload}'")
    assert len(result.decoded_views) == 1


def test_all_terms_reference_known_rules() -> None:
    known = {"N-BASE-001", "N-CASEFOLD-001", "N-DEOBF-001", "N-SUBWORD-001"}
    payload = _b64("Ne^tStat -ano")
    result = analyze(f'po"wer"shell {payload} --x=%41%42')
    tokens = list(result.tokens)
    for view in result.decoded_views:
        tokens.extend(view.tokens)
    for token in tokens:
        for term in token.terms:
            assert term.rules and set(term.rules) <= known


def test_view_chain_reaches_root() -> None:
    inner = _b64("Write-Output hello!")
    result = analyze("run " + inner.replace("=", "%3D"))
    assert len(result.decoded_views) == 2
    views = {v.id: v for v in result.decoded_views}
    for view in result.decoded_views:
        depth = 0
        current = view
        while current is not None:
            depth += 1
            parent = current.parent_view_id
            current = views[parent] if parent is not None else None
        assert depth == view.depth


def test_json_round_trip_keeps_provenance() -> None:
    payload = _b64(PAYLOAD, "utf-16le")
    data = analyze(f"powershell -enc {payload}").to_dict()
    view = data["decoded_views"][0]
    assert view["source_span"] == [16, 16 + len(payload)]
    assert view["hint"] == "C-HINT-ENC-001"
    assert view["mapping"] == "range"
