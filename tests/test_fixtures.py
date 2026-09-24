"""固定样例：``tests/fixtures/cases.jsonl`` 覆盖规划第 4 节列出的全部类别。

每行一个样例。编码载荷在加载时由 ``payloads`` 规格生成（内容均为无害文本），
文本与期望中的 ``@NAME@`` 替换为载荷，``"@span:NAME@"`` 替换为载荷在输入中的区间。
期望按子集比较：字典只比较列出的键，列表要求长度一致并逐项比较，
期望为字符串而实际为字典时比较其 ``raw``。CLI 样例只启动本项目 CLI 自身。
"""

from __future__ import annotations

import base64
import json
import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from invariants import check_result

from cmdline_parser import AnalyzerOptions, analyze
from cmdline_parser.decoders import DecodeSuccess, base64_decoder, escape_decoder, percent_decoder

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "fixtures" / "cases.jsonl"
SRC = HERE.parent / "src"

CATEGORIES = {
    "empty",
    "plain",
    "missing_operator",
    "unclosed_quote",
    "bad_quotes",
    "empty_quotes",
    "truncation",
    "caret",
    "escape_counterexample",
    "quote_insertion",
    "paths",
    "unicode",
    "base64",
    "percent",
    "escape",
    "partial",
    "misdetection",
    "mixed",
    "limits",
    "cli",
}
CASE_KEYS = {"id", "category", "note", "text", "options", "payloads", "expect", "all_prefixes", "cli"}
EXPECT_KEYS = {
    "raw",
    "tokens",
    "token_spans",
    "symbols",
    "groups",
    "views",
    "notices",
    "processing",
    "terms",
    "term_detail",
    "terms_include",
    "terms_exclude",
    "view_terms_include",
    "same_terms_as",
}
CLI_KEYS = {"args", "stdin", "stdin_hex", "file", "exit", "stderr_contains"}

_DECODE = {
    "base64": base64_decoder.decode,
    "base64url": base64_decoder.decode,
    "percent": percent_decoder.decode,
    "hex_escape": escape_decoder.decode_hex,
    "unicode_escape": escape_decoder.decode_unicode,
}
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")


# ---------------------------------------------------------------- 载荷与加载


def _as_bytes(value: str | bytes) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value


def _as_text(value: str | bytes) -> str:
    assert isinstance(value, str), "payload step expects text"
    return value


def _build_payload(spec: dict[str, Any]) -> str:
    """按顺序执行编码步骤，生成载荷字符串。"""
    value: str | bytes = spec["text"]
    for step in spec.get("steps", []):
        if step in ("utf-8", "utf-16le"):
            value = _as_text(value).encode(step)
        elif step == "b64":
            value = base64.b64encode(_as_bytes(value)).decode("ascii")
        elif step == "b64url":
            value = base64.urlsafe_b64encode(_as_bytes(value)).decode("ascii").rstrip("=")
        elif step == "pct":
            value = "".join(f"%{byte:02X}" for byte in _as_bytes(value))
        elif step == "urlquote":
            value = "".join(
                char if char in _UNRESERVED else "".join(f"%{b:02X}" for b in char.encode("utf-8"))
                for char in _as_text(value)
            )
        elif step.startswith("cut:"):
            value = _as_text(value)[: -int(step[4:])]
        else:
            raise ValueError(f"unknown payload step {step!r}")
    return _as_text(value)


def _substitute(value: Any, payloads: dict[str, str], text: str | None) -> Any:
    if isinstance(value, str):
        if value.startswith("@span:") and value.endswith("@"):
            payload = payloads[value[6:-1]]
            assert text is not None
            start = text.index(payload)
            return [start, start + len(payload)]
        for name, payload in payloads.items():
            value = value.replace(f"@{name}@", payload)
        return value
    if isinstance(value, list):
        return [_substitute(item, payloads, text) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, payloads, text) for key, item in value.items()}
    return value


def _cli_input_text(spec: dict[str, Any]) -> str | None:
    """CLI 实际分析的文本；输入无法按 UTF-8 解码或文件不存在时返回 None。"""
    args = spec.get("args", [])
    if "--text" in args:
        return args[args.index("--text") + 1]
    if "--file" in args:
        return spec.get("file")
    if "stdin_hex" in spec:
        try:
            return bytes.fromhex(spec["stdin_hex"]).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return spec.get("stdin", "")


def _load_cases() -> list[dict[str, Any]]:
    cases = []
    with CASES_PATH.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            case = json.loads(line)
            assert set(case) <= CASE_KEYS, (number, set(case) - CASE_KEYS)
            assert set(case.get("expect", {})) <= EXPECT_KEYS, (number, case["id"])
            payloads = {name: _build_payload(spec) for name, spec in case.get("payloads", {}).items()}
            if "cli" in case:
                assert set(case["cli"]) <= CLI_KEYS, (number, case["id"])
                case["cli"] = _substitute(case["cli"], payloads, None)
                text = _cli_input_text(case["cli"])
            else:
                text = case["text"] = _substitute(case["text"], payloads, None)
            case["expect"] = _substitute(case.get("expect", {}), payloads, text)
            cases.append(case)
    return cases


CASES = _load_cases()


def _params(cli: bool) -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in CASES if ("cli" in case) == cli]


# ---------------------------------------------------------------- 期望比较


def _match(actual: Any, expected: Any, where: str = "$") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), (where, actual)
        for key, value in expected.items():
            assert key in actual, f"{where}.{key} is missing"
            _match(actual[key], value, f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), (where, actual, expected)
        for index, (item, wanted) in enumerate(zip(actual, expected)):
            _match(item, wanted, f"{where}[{index}]")
    elif isinstance(expected, str) and isinstance(actual, dict):
        assert actual.get("raw") == expected, (where, actual, expected)
    else:
        assert type(actual) is type(expected) and actual == expected, (where, actual, expected)


def _term_values(tokens: list[dict[str, Any]]) -> set[str]:
    return {term["value"] for token in tokens for term in token["terms"]}


def _check_expect(
    expect: dict[str, Any], data: dict[str, Any], options: AnalyzerOptions
) -> None:
    for key in ("raw", "tokens", "symbols", "groups", "notices", "processing"):
        if key in expect:
            _match(data[key], expect[key], f"$.{key}")
    if "views" in expect:
        _match(data["decoded_views"], expect["views"], "$.decoded_views")
    if "token_spans" in expect:
        assert [token["span"] for token in data["tokens"]] == expect["token_spans"]
    for index, values in expect.get("terms", {}).items():
        actual = [term["value"] for term in data["tokens"][int(index)]["terms"]]
        assert actual == values, (index, actual)
    for detail in expect.get("term_detail", []):
        detail = dict(detail)
        token = data["tokens"][detail.pop("token")]
        matches = [term for term in token["terms"] if term["value"] == detail["value"]]
        assert len(matches) == 1, (detail, token["terms"])
        _match(matches[0], detail, f"$.term[{detail['value']!r}]")
    root_terms = _term_values(data["tokens"])
    for value in expect.get("terms_include", []):
        assert value in root_terms, (value, sorted(root_terms))
    for value in expect.get("terms_exclude", []):
        assert value not in root_terms, value
    if "view_terms_include" in expect:
        view_terms = set()
        for view in data["decoded_views"]:
            view_terms |= _term_values(view["tokens"])
        for value in expect["view_terms_include"]:
            assert value in view_terms, (value, sorted(view_terms))
    if "same_terms_as" in expect:
        other = analyze(expect["same_terms_as"], options=options).to_dict()
        assert [[t["value"] for t in token["terms"]] for token in data["tokens"]] == [
            [t["value"] for t in token["terms"]] for token in other["tokens"]
        ]


def _check_provenance(text: str, data: dict[str, Any]) -> None:
    """每个视图的来源区间重新解码后得到相同的文本与标记。"""
    texts: dict[str | None, str] = {None: text}
    for view in data["decoded_views"]:
        parent = texts[view["parent_view_id"]]
        start, stop = view["source_span"]
        outcome = _DECODE[view["encoding"]](parent[start:stop], view["hint"] is not None)
        assert isinstance(outcome, DecodeSuccess), (view["id"], outcome)
        assert (outcome.text, outcome.status, outcome.charset, outcome.rule, outcome.encoding) == (
            view["text"],
            view["status"],
            view["charset"],
            view["rule"],
            view["encoding"],
        ), view["id"]
        texts[view["id"]] = view["text"]


def _check_prefixes(text: str, options: AnalyzerOptions) -> None:
    """任意截断的前缀都能正常分析；在前缀内已结束的片段与完整输入一致，不补全。"""
    full = analyze(text, options=options)
    for cut in range(len(text) + 1):
        prefix = text[:cut]
        result = analyze(prefix, options=options)
        check_result(prefix, result, options)
        for name in ("tokens", "symbols"):
            expected = [item.to_dict() for item in getattr(full, name) if item.span[1] < cut]
            actual = [item.to_dict() for item in getattr(result, name) if item.span[1] < cut]
            assert actual == expected, (cut, name)


# ---------------------------------------------------------------- 测试


def test_fixture_file_covers_every_category() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))
    assert len(CASES) >= 60
    assert {case["category"] for case in CASES} == CATEGORIES


@pytest.mark.parametrize("case", _params(cli=False))
def test_library_case(case: dict[str, Any]) -> None:
    options = AnalyzerOptions(**case.get("options", {}))
    text = case["text"]
    result = analyze(text, options=options)
    check_result(text, result, options)
    data = result.to_dict()
    _check_provenance(text, data)
    _check_expect(case["expect"], data, options)
    if case.get("all_prefixes"):
        _check_prefixes(text, options)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) if not existing else f"{SRC}{os.pathsep}{existing}"
    return env


@pytest.mark.parametrize("case", _params(cli=True))
def test_cli_case(case: dict[str, Any], tmp_path: Path) -> None:
    spec = case["cli"]
    paths = {"@file@": tmp_path / "input.txt", "@missing@": tmp_path / "missing.txt"}
    if "file" in spec:
        paths["@file@"].write_bytes(spec["file"].encode("utf-8"))
    args = [str(paths[arg]) if arg in paths else arg for arg in spec.get("args", [])]
    if "stdin" in spec:
        stdin = spec["stdin"].encode("utf-8")
    else:
        stdin = bytes.fromhex(spec.get("stdin_hex", ""))
    proc = subprocess.run(
        [sys.executable, "-m", "cmdline_parser", *args],
        input=stdin,
        capture_output=True,
        env=_env(),
        timeout=60,
        check=False,
    )
    expected_exit = spec.get("exit", 0)
    assert proc.returncode == expected_exit, proc.stderr.decode("utf-8", "replace")
    if "stderr_contains" in spec:
        assert spec["stderr_contains"] in proc.stderr.decode("utf-8", "replace")
    if expected_exit != 0:
        assert proc.stdout == b""
        return

    assert proc.stderr == b""
    assert proc.stdout.endswith(b"\n")
    data = json.loads(proc.stdout.decode("utf-8"))
    options = AnalyzerOptions(decode="--no-decode" not in args)
    text = _cli_input_text(spec)
    assert text is not None
    assert data == analyze(text, options=options).to_dict()
    _check_expect(case["expect"], data, options)
