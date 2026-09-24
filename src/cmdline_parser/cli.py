"""命令行入口：``cmdline-parser`` 与 ``python -m cmdline_parser``。

stdout 只输出一行（或 ``--pretty`` 时多行）结果 JSON；运行错误写入 stderr。

退出码：0 完成分析（含语法损坏、部分解码与资源受限）；1 运行错误
（文件读取、输入字符编码等）；2 参数使用错误。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .analyzer import RULES_VERSION, analyze
from .limits import AnalyzerOptions
from .models import SCHEMA_VERSION

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cmdline-parser",
        description=(
            "Analyze one command line for log search: fault-tolerant tokens, search "
            "terms and statically decoded candidates, printed as JSON. "
            "Reads UTF-8 from stdin when neither --text nor --file is given."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", metavar="TEXT", help="analyze TEXT")
    source.add_argument("--file", metavar="PATH", help="analyze the whole UTF-8 file at PATH")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    parser.add_argument(
        "--no-decode", action="store_true", help="disable automatic decoding of encoded candidates"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=(
            f"cmdline-parser {__version__} (rules {RULES_VERSION}, schema {SCHEMA_VERSION})"
        ),
    )
    return parser


def _read_input(args: argparse.Namespace) -> str:
    """读取一条完整文本；不去除首尾空白，也不去除 UTF-8 BOM（BOM 按原文保留）。"""
    if args.text is not None:
        return args.text
    if args.file is not None:
        with open(args.file, "rb") as handle:
            data = handle.read()
    else:
        data = sys.stdin.buffer.read()
    return data.decode("utf-8")


def _error(message: str) -> int:
    sys.stderr.write(f"cmdline-parser: error: {message}\n")
    sys.stderr.flush()
    return EXIT_RUNTIME_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        text = _read_input(args)
    except UnicodeDecodeError as exc:
        return _error(f"input is not valid UTF-8 (byte offset {exc.start}): {exc.reason}")
    except OSError as exc:
        where = f" {exc.filename}" if exc.filename else ""
        return _error(f"cannot read input{where}: {exc.strerror or exc}")

    result = analyze(text, options=AnalyzerOptions(decode=not args.no_decode))
    try:
        payload = result.to_json(pretty=args.pretty).encode("utf-8")
    except UnicodeEncodeError:
        # 仅在 --text 携带无法表示为 UTF-8 的代理字符时出现。
        return _error("input contains characters that cannot be encoded as UTF-8")

    stdout = sys.stdout.buffer
    stdout.write(payload + b"\n")
    stdout.flush()
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
