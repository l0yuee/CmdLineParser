"""分析结果的数据模型与 JSON 序列化。

所有位置均为 Python 字符串索引（Unicode 码点），区间为左闭右开 ``[start, end)``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1"

Span = tuple[int, int]

# 搜索词项类别，输出顺序固定为 original → casefold → alias → subword。
KIND_ORIGINAL = "original"
KIND_CASEFOLD = "casefold"
KIND_ALIAS = "alias"
KIND_SUBWORD = "subword"

# 解码状态：只描述已观察载荷的处理情况，不代表日志本身完整。
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"

# 通知级别。
LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_LIMIT = "limit"


def _span(span: Span | None) -> list[int] | None:
    return None if span is None else [span[0], span[1]]


@dataclass(slots=True)
class Term:
    """一个搜索词项。同一 Token 的全部词项共享该 Token 的位置。"""

    value: str
    kind: str
    rules: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "kind": self.kind, "rules": list(self.rules)}


@dataclass(slots=True)
class Token:
    """基础扫描得到的原文片段，满足 ``raw == text[span[0]:span[1]]``。"""

    raw: str
    span: Span
    position: int
    terms: list[Term] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "span": _span(self.span),
            "position": self.position,
            "terms": [term.to_dict() for term in self.terms],
        }


@dataclass(slots=True)
class Group:
    """启发式引号分组；未闭合时延伸到扫描末尾且 ``closed`` 为 False。"""

    raw: str
    span: Span
    quote: str
    content: str
    content_span: Span
    closed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "span": _span(self.span),
            "quote": self.quote,
            "content": self.content,
            "content_span": _span(self.content_span),
            "closed": self.closed,
        }


@dataclass(slots=True)
class Symbol:
    """原文中的连续边界符号，不声明任何执行语义。"""

    raw: str
    span: Span

    def to_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "span": _span(self.span)}


@dataclass(slots=True)
class Notice:
    """不完整片段、解码异常或资源限制等信息，不是整条记录的失败。"""

    code: str
    level: str
    message: str
    view_id: str | None = None
    span: Span | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "level": self.level,
            "message": self.message,
            "view_id": self.view_id,
            "span": _span(self.span),
            "details": dict(self.details),
        }


@dataclass(slots=True)
class DecodedView:
    """一次解码得到的候选文本及其分析结果。

    ``source_span`` 位于父视图坐标系中（``parent_view_id`` 为 None 时即原文），
    只做整段区间映射，不声明逐字符对应关系。
    """

    id: str
    parent_view_id: str | None
    depth: int
    source_span: Span
    source_kind: str
    candidate_rule: str
    rule: str
    encoding: str
    charset: str
    status: str
    hint: str | None
    text: str
    tokens: list[Token] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    mapping: str = "range"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_view_id": self.parent_view_id,
            "depth": self.depth,
            "source_span": _span(self.source_span),
            "source_kind": self.source_kind,
            "candidate_rule": self.candidate_rule,
            "rule": self.rule,
            "encoding": self.encoding,
            "charset": self.charset,
            "status": self.status,
            "hint": self.hint,
            "mapping": self.mapping,
            "text": self.text,
            "tokens": [token.to_dict() for token in self.tokens],
            "groups": [group.to_dict() for group in self.groups],
            "symbols": [symbol.to_dict() for symbol in self.symbols],
        }


@dataclass(slots=True)
class Processing:
    """实际扫描范围、规则版本、资源使用与受限情况。"""

    rules_version: str
    input_length: int
    scanned_span: Span
    scan_complete: bool
    decode_enabled: bool
    limited: bool
    limits: list[dict[str, Any]]
    stats: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules_version": self.rules_version,
            "input_length": self.input_length,
            "scanned_span": _span(self.scanned_span),
            "scan_complete": self.scan_complete,
            "decode_enabled": self.decode_enabled,
            "limited": self.limited,
            "limits": [dict(item) for item in self.limits],
            "stats": dict(self.stats),
        }


@dataclass(slots=True)
class AnalysisResult:
    """:func:`cmdline_parser.analyze` 的返回值。"""

    raw: str
    tokens: list[Token]
    groups: list[Group]
    symbols: list[Symbol]
    decoded_views: list[DecodedView]
    notices: list[Notice]
    processing: Processing
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "raw": self.raw,
            "tokens": [token.to_dict() for token in self.tokens],
            "groups": [group.to_dict() for group in self.groups],
            "symbols": [symbol.to_dict() for symbol in self.symbols],
            "decoded_views": [view.to_dict() for view in self.decoded_views],
            "notices": [notice.to_dict() for notice in self.notices],
            "processing": self.processing.to_dict(),
        }

    def to_json(self, *, pretty: bool = False) -> str:
        if pretty:
            return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
