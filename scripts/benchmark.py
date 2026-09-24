"""性能基准：记录运行环境、耗时、峰值内存与输出规模，检查输出有界且耗时增长不呈明显二次关系。

用法::

    python scripts/benchmark.py                       # 每个输入重复 3 次，取最小耗时
    python scripts/benchmark.py --quick               # 每个输入只运行 1 次
    python scripts/benchmark.py --json bench.json     # 另存机器可读结果

结果只反映运行基准的那台机器，不作为吞吐量承诺。编码载荷全部由无害文本
（``Write-Output hello`` 等）生成；基准只调用分析器本身，不执行命令、不访问网络。
任一检查失败时退出码为 1。
"""

from __future__ import annotations

import argparse
import base64
import gc
import json
import math
import os
import platform
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import cmdline_parser
except ImportError:  # 未安装时直接从源码目录运行
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    import cmdline_parser

from cmdline_parser import RULES_VERSION, SCHEMA_VERSION, AnalysisResult, AnalyzerOptions, analyze

KIB = 1024
MIB = 1024 * KIB

DEFAULT_OPTIONS = AnalyzerOptions()
# 放开输出条目上限，使整段输入都被扫描并输出，用于观察耗时随输入长度的增长。
FULL_OUTPUT_OPTIONS = AnalyzerOptions(max_output_items=10**9)

# 相邻规模的耗时增长指数 log(t2/t1) / log(n2/n1)：线性约为 1，二次约为 2。
# 超过该值判定为异常增长；对 16 倍输入相当于耗时超过 64 倍。
MAX_GROWTH_EXPONENT = 1.5

# ---------------------------------------------------------------- 输入生成

PLAIN_UNITS = (
    'cmd.exe /c "ne^tstat -ano | fi^ndstr 443" ',
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command Get-ChildItem C:\\Windows\\Temp ",
    "/usr/bin/curl -s http://example.test/index.html?a=1&b=2 -o /tmp/index.html; ",
    "reg query HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run /v Updater ",
    "echo it's done && type C:\\Users\\Public\\notes.txt ",
)

SYMBOL_UNIT = "a|b&&c;d>e<f(g)[h]{i}\"j\"'k'|&;<>()[]{} "


def plain_unit(index: int) -> str:
    return PLAIN_UNITS[index % len(PLAIN_UNITS)]


def symbol_unit(index: int) -> str:
    return SYMBOL_UNIT


def encoding_unit(index: int) -> str:
    """每个单元的载荷各不相同，避免被按文本去重。"""
    text = f"Write-Output hello {index}"
    utf16 = base64.b64encode(text.encode("utf-16-le")).decode("ascii")
    utf8 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    short = f"echo {index}"
    percent = "".join(f"%{byte:02X}" for byte in short.encode("utf-8"))
    hex_escape = "".join(f"\\x{byte:02x}" for byte in short.encode("utf-8"))
    unicode_escape = "".join(f"\\u{ord(char):04x}" for char in short)
    return f'powershell -enc {utf16} --data={utf8} "{percent}" {hex_escape} {unicode_escape} '


def repeat_to(unit: Callable[[int], str], size: int) -> str:
    """重复单元直到恰好 ``size`` 个字符（末尾单元可能被截断，这也是常见的日志形态）。"""
    parts: list[str] = []
    total = 0
    index = 0
    while total < size:
        part = unit(index)
        parts.append(part)
        total += len(part)
        index += 1
    return "".join(parts)[:size]


def single_payload(size: int) -> str:
    """一个 ``-enc`` 后接单个长 Base64 载荷，总长度不超过 ``size``。"""
    prefix = "powershell -enc "
    unit = "Write-Output hello; "
    repeat = (size - len(prefix)) * 3 // 4 // (2 * len(unit))
    payload = base64.b64encode((unit * repeat).encode("utf-16-le")).decode("ascii")
    return prefix + payload


# ---------------------------------------------------------------- 用例


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    series: str | None  # 同一系列的用例参与耗时增长检查
    text: str
    options: AnalyzerOptions
    options_label: str


def build_cases() -> list[Case]:
    cases: list[Case] = []

    def add(name: str, text: str, *, series: str | None = None, full: bool = False) -> None:
        options, label = (FULL_OUTPUT_OPTIONS, "full-output") if full else (DEFAULT_OPTIONS, "default")
        cases.append(Case(name, series, text, options, label))

    # 默认选项：检查输出有界。
    for label, size in (("1k", KIB), ("4k", 4 * KIB), ("64k", 64 * KIB), ("1m", MIB)):
        add(f"plain-{label}", repeat_to(plain_unit, size))
    add("symbol-dense-64k", repeat_to(symbol_unit, 64 * KIB))
    add("symbol-dense-1m", repeat_to(symbol_unit, MIB))
    add("encoding-dense-64k", repeat_to(encoding_unit, 64 * KIB), series="encoding-dense")
    add("encoding-dense-1m", repeat_to(encoding_unit, MIB), series="encoding-dense")
    add("single-payload-60k", single_payload(60_000))  # 不超过 max_candidate_chars，会被解码
    add("single-payload-1m", single_payload(MIB))  # 超过 max_candidate_chars，只记录受限

    # 放开输出条目上限：检查耗时随输入增长。高密度符号只到 256 KiB，控制峰值内存。
    for label, size in (("4k", 4 * KIB), ("64k", 64 * KIB), ("1m", MIB)):
        add(f"plain-full-{label}", repeat_to(plain_unit, size), series="plain", full=True)
    for label, size in (("16k", 16 * KIB), ("256k", 256 * KIB)):
        add(f"symbol-full-{label}", repeat_to(symbol_unit, size), series="symbol-dense", full=True)
    return cases


# ---------------------------------------------------------------- 测量与检查


def check_bounds(result: AnalysisResult, options: AnalyzerOptions) -> list[str]:
    """输出规模不得超过各项资源上限。"""
    stats = result.processing.stats
    ordinary = [notice for notice in result.notices if notice.code != "LIMIT_REACHED"]
    layers = [result.tokens] + [view.tokens for view in result.decoded_views]
    checks = [
        (stats["output_items"] <= options.max_output_items, "output_items exceeds max_output_items"),
        (
            len(result.decoded_views) == stats["decoded_views"] <= options.max_decoded_views,
            "decoded_views exceeds max_decoded_views",
        ),
        (stats["decoded_bytes"] <= options.max_decoded_bytes, "decoded_bytes exceeds max_decoded_bytes"),
        (
            stats["decode_attempts"] <= options.max_decode_attempts,
            "decode_attempts exceeds max_decode_attempts",
        ),
        (len(ordinary) <= options.max_notices, "notices exceed max_notices"),
        (result.processing.scanned_span[1] <= options.max_scan_chars, "scan exceeds max_scan_chars"),
        (
            all(view.depth <= options.max_decode_depth for view in result.decoded_views),
            "view depth exceeds max_decode_depth",
        ),
        (
            all(len(token.terms) <= options.max_terms_per_token for tokens in layers for token in tokens),
            "terms exceed max_terms_per_token",
        ),
        (result.processing.limited == bool(result.processing.limits), "limited flag inconsistent"),
    ]
    return [message for ok, message in checks if not ok]


def measure(case: Case, repeats: int) -> dict[str, Any]:
    best = math.inf
    result: AnalysisResult | None = None
    for _ in range(repeats):
        result = None
        gc.collect()
        start = time.perf_counter()
        result = analyze(case.text, options=case.options)
        best = min(best, time.perf_counter() - start)

    # 峰值内存单独测量：tracemalloc 会拖慢执行，不与计时混在一起。
    result = None
    gc.collect()
    tracemalloc.start()
    try:
        result = analyze(case.text, options=case.options)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    start = time.perf_counter()
    payload = result.to_json()
    json_seconds = time.perf_counter() - start

    stats = result.processing.stats
    return {
        "name": case.name,
        "series": case.series,
        "options": case.options_label,
        "chars": len(case.text),
        "repeats": repeats,
        "analyze_seconds_min": best,
        "to_json_seconds": json_seconds,
        "peak_bytes": peak,
        "json_bytes": len(payload.encode("utf-8")),
        "output_items": stats["output_items"],
        "tokens": stats["tokens"],
        "decode_attempts": stats["decode_attempts"],
        "decoded_views": stats["decoded_views"],
        "decoded_bytes": stats["decoded_bytes"],
        "notices": len(result.notices),
        "limits": [item["name"] for item in result.processing.limits],
        "problems": check_bounds(result, case.options),
    }


def growth(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一系列中相邻规模的耗时增长指数。"""
    series: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record["series"]:
            series.setdefault(record["series"], []).append(record)
    rows = []
    for name, items in series.items():
        items.sort(key=lambda item: item["chars"])
        for small, large in zip(items, items[1:]):
            size_ratio = large["chars"] / small["chars"]
            time_ratio = large["analyze_seconds_min"] / small["analyze_seconds_min"]
            exponent = math.log(time_ratio) / math.log(size_ratio)
            rows.append(
                {
                    "series": name,
                    "from": small["name"],
                    "to": large["name"],
                    "size_ratio": size_ratio,
                    "time_ratio": time_ratio,
                    "exponent": exponent,
                    "ok": exponent <= MAX_GROWTH_EXPONENT,
                }
            )
    return rows


def environment() -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_build": " ".join(platform.python_build()),
        "executable": sys.executable,
        "cmdline_parser": cmdline_parser.__version__,
        "rules_version": RULES_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


# ---------------------------------------------------------------- 输出


def _size(count: int) -> str:
    if count >= MIB:
        return f"{count / MIB:.1f}M"
    if count >= KIB:
        return f"{count / KIB:.1f}K"
    return str(count)


def print_report(env: dict[str, Any], records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    print("Environment")
    for key, value in env.items():
        print(f"  {key:<22} {value}")

    print("\nResults (time = min of repeats; peak memory measured in a separate tracemalloc run)")
    header = (
        f"  {'case':<20} {'opts':<11} {'chars':>7} {'analyze ms':>11} {'us/KiB':>8} {'json ms':>9}"
        f" {'peak MiB':>9} {'json':>7} {'items':>7} {'attempts':>8} {'views':>5} {'dec B':>7}  limits"
    )
    print(header)
    for record in records:
        seconds = record["analyze_seconds_min"]
        print(
            f"  {record['name']:<20} {record['options']:<11} {_size(record['chars']):>7}"
            f" {seconds * 1000:>11.2f} {seconds * 1e6 / (record['chars'] / KIB):>8.1f}"
            f" {record['to_json_seconds'] * 1000:>9.2f} {record['peak_bytes'] / MIB:>9.2f}"
            f" {_size(record['json_bytes']):>7} {record['output_items']:>7}"
            f" {record['decode_attempts']:>8} {record['decoded_views']:>5} {record['decoded_bytes']:>7}"
            f"  {','.join(record['limits']) or '-'}"
        )

    print(f"\nGrowth (exponent = log(time ratio) / log(size ratio); fail above {MAX_GROWTH_EXPONENT})")
    for row in rows:
        status = "ok" if row["ok"] else "FAIL"
        print(
            f"  {row['series']:<15} {row['from']:>20} -> {row['to']:<20}"
            f" size x{row['size_ratio']:<6.1f} time x{row['time_ratio']:<8.2f}"
            f" exponent {row['exponent']:.2f}  {status}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark cmdline_parser.analyze on synthetic inputs.")
    parser.add_argument("--quick", action="store_true", help="run each input once instead of 3 times")
    parser.add_argument("--repeats", type=int, help="timed runs per input (default: 3, or 1 with --quick)")
    parser.add_argument("--json", type=Path, metavar="PATH", help="also write machine-readable results")
    args = parser.parse_args()

    repeats = args.repeats if args.repeats is not None else (1 if args.quick else 3)
    if repeats < 1:
        parser.error("--repeats must be >= 1")

    env = environment()
    records = []
    for case in build_cases():
        print(f"running {case.name} ({_size(len(case.text))} chars) ...", file=sys.stderr, flush=True)
        records.append(measure(case, repeats))
    rows = growth(records)

    print_report(env, records, rows)

    problems = [f"{record['name']}: {problem}" for record in records for problem in record["problems"]]
    problems += [
        f"{row['series']}: {row['from']} -> {row['to']} growth exponent {row['exponent']:.2f}"
        for row in rows
        if not row["ok"]
    ]
    if args.json is not None:
        document = {"environment": env, "results": records, "growth": rows, "problems": problems}
        args.json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if problems:
        print("\nFAILED checks:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("\nAll checks passed: outputs stay within limits and growth is not quadratic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
