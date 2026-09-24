"""示例：分析一条命令行，分别消费原文词、别名和解码词。

用法（推荐通过 stdin 传入样本，避免外层 Shell 提前解释引号、caret、管道符）::

    printf '%s' 'ne^tstat -ano | fi^ndstr "443' | python examples/analyze_one.py
    python examples/analyze_one.py --text 'po"wer"shell -enc VwByAGkAdABlAC0ATwB1AHQAcAB1AHQAIABoAGUAbABsAG8A'
    python examples/analyze_one.py --jsonl < sample.txt

要点：

- 原文词（original / casefold / subword）来自原文片段，可直接用于检索原文；
- 别名（alias）是去混淆后的搜索候选，与原始词共享同一位置，不能用于推断词序或相邻关系；
- 解码词来自解码视图，其位置属于视图文本；需沿 ``parent_view_id`` 与 ``source_span``
  逐层回到原文，且只有整段区间映射。所有解码结果都只是候选，不代表实际执行内容。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    from cmdline_parser import AnalysisResult, analyze
except ImportError:  # 未安装时直接从源码目录运行
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from cmdline_parser import AnalysisResult, analyze


def root_span(result: AnalysisResult, view_id: str) -> tuple[int, int]:
    """沿来源链把解码视图映射回原文区间（整段映射，不是逐字符对应）。"""
    views = {view.id: view for view in result.decoded_views}
    view = views[view_id]
    while view.parent_view_id is not None:
        view = views[view.parent_view_id]
    return view.source_span


def provenance(result: AnalysisResult, view_id: str) -> list[str]:
    """从最外层原文到该视图的来源链描述。"""
    views = {view.id: view for view in result.decoded_views}
    chain: list[str] = []
    current: str | None = view_id
    while current is not None:
        view = views[current]
        parent = view.parent_view_id or "raw"
        start, end = view.source_span
        chain.append(
            f"{parent}[{start}:{end}] --{view.encoding}/{view.charset} ({view.rule})--> {view.id}"
        )
        current = view.parent_view_id
    return list(reversed(chain))


def index_records(result: AnalysisResult) -> Iterator[dict[str, Any]]:
    """把分析结果展开为适合写入检索系统的记录。

    ``field`` 区分三类用途：``term`` 原文词、``alias`` 去混淆别名、``decoded`` 解码词。
    ``span`` 始终是原文区间；解码词另外给出视图内位置 ``view_span``。
    """
    for token in result.tokens:
        for term in token.terms:
            yield {
                "field": "alias" if term.kind == "alias" else "term",
                "value": term.value,
                "kind": term.kind,
                "rules": term.rules,
                "position": token.position,
                "span": list(token.span),
            }
    for view in result.decoded_views:
        origin = list(root_span(result, view.id))
        for token in view.tokens:
            for term in token.terms:
                yield {
                    "field": "decoded",
                    "value": term.value,
                    "kind": term.kind,
                    "rules": term.rules,
                    "view_id": view.id,
                    "view_status": view.status,
                    "view_span": list(token.span),
                    "span": origin,
                    "candidate": True,
                }


def _values(terms: list[Any], *kinds: str) -> str:
    return ", ".join(term.value for term in terms if term.kind in kinds) or "-"


def print_report(result: AnalysisResult) -> None:
    print(f"原文 ({len(result.raw)} 字符): {result.raw!r}")

    print("\n[原文词] position span raw -> original | casefold | subword")
    for token in result.tokens:
        print(
            f"  {token.position:>3} {list(token.span)!s:<10} {token.raw!r}"
            f" -> {_values(token.terms, 'original')}"
            f" | {_values(token.terms, 'casefold')}"
            f" | {_values(token.terms, 'subword')}"
        )

    print("\n[别名] 与原始词共享位置，只是搜索候选")
    aliases = [(token, term) for token in result.tokens for term in token.terms if term.kind == "alias"]
    for token, term in aliases:
        print(f"  {token.position:>3} {token.raw!r} -> {term.value!r}  {term.rules}")
    if not aliases:
        print("  （无）")

    print("\n[符号] 只记录原文位置，不代表执行语义")
    print("  " + (", ".join(f"{s.raw!r}@{list(s.span)}" for s in result.symbols) or "（无）"))

    print("\n[解码视图] 静态解码候选，不代表实际执行内容")
    for view in result.decoded_views:
        start, end = root_span(result, view.id)
        hint = f" hint={view.hint}" if view.hint else ""
        print(f"  {view.id}: {view.status}{hint}，来自原文 [{start}:{end}]")
        for step in provenance(result, view.id):
            print(f"      {step}")
        print(f"      text: {view.text!r}")
        for token in view.tokens:
            print(
                f"      {token.position:>3} {token.raw!r}"
                f" -> {_values(token.terms, 'original', 'casefold', 'alias')}"
            )
    if not result.decoded_views:
        print("  （无）")

    print("\n[通知]")
    for notice in result.notices:
        where = notice.view_id or "raw"
        span = list(notice.span) if notice.span else None
        print(f"  {notice.level:<7} {notice.code} @{where}{span} {notice.details}")
    if not result.notices:
        print("  （无）")

    processing = result.processing
    print(
        f"\n[处理] rules {processing.rules_version}，扫描 {list(processing.scanned_span)}，"
        f"受限: {[item['name'] for item in processing.limits] or '否'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze one command line and show how to consume the result.")
    parser.add_argument("--text", help="text to analyze (default: read UTF-8 from stdin)")
    parser.add_argument("--jsonl", action="store_true", help="print index records as JSON lines")
    args = parser.parse_args()

    text = args.text if args.text is not None else sys.stdin.buffer.read().decode("utf-8")
    result = analyze(text)

    # 控制台编码无法表示某些字符时转义输出，而不是中断示例。
    sys.stdout.reconfigure(errors="backslashreplace")
    if args.jsonl:
        for record in index_records(result):
            print(json.dumps(record, ensure_ascii=False))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
