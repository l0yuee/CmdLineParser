"""确定性的容错基础扫描与引号分组。

两类扫描相互独立：基础扫描不理会引号，引号分组不模拟任何 Shell 的转义规则。
任何输入都能完成扫描，不存在"语法错误"。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

RULE_BOUNDARY = "S-BOUNDARY-001"
RULE_SYMBOL = "S-SYMBOL-001"
RULE_GROUP = "S-GROUP-001"

BOUNDARY_SYMBOLS = "|&;<>()[]{}"
QUOTES = "'\""

TOKEN = "token"
SYMBOL = "symbol"

_SYMBOL_CLASS = r"|&;<>()\[\]{}"
# Unicode 空白与边界符号切分基础片段；连续边界符号记为一个符号片段。
# 在 str 模式下 \s 与 str.isspace() 一致。
_SEGMENT_RE = re.compile(rf"(?P<sym>[{_SYMBOL_CLASS}]+)|(?P<tok>[^\s{_SYMBOL_CLASS}]+)")
_BOUNDARY_RE = re.compile(rf"[\s{_SYMBOL_CLASS}]")
_QUOTE_RE = re.compile(r"['\"]")


def iter_segments(text: str, end: int) -> Iterator[tuple[str, int, int]]:
    """按位置顺序产出 ``(kind, start, end)``，kind 为 TOKEN 或 SYMBOL。"""
    for match in _SEGMENT_RE.finditer(text, 0, end):
        kind = SYMBOL if match.lastgroup == "sym" else TOKEN
        yield kind, match.start(), match.end()


def iter_groups(text: str, end: int) -> Iterator[tuple[str, int, int, bool]]:
    """产出 ``(quote, start, end, closed)``。

    同类 ASCII 引号配对，组内的异类引号视为内容；未闭合的组延伸到 ``end``。
    """
    pos = 0
    while True:
        match = _QUOTE_RE.search(text, pos, end)
        if match is None:
            return
        quote = match.group()
        start = match.start()
        close = text.find(quote, start + 1, end)
        if close < 0:
            yield quote, start, end, False
            return
        yield quote, start, close + 1, True
        pos = close + 1


def contains_boundary(value: str) -> bool:
    """片段是否包含基础扫描边界（即跨越多个基础片段）。"""
    return _BOUNDARY_RE.search(value) is not None
