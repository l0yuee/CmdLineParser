"""可调选项与单次分析的资源预算。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .models import LEVEL_LIMIT, Notice, Span

_SWITCHES = ("decode", "subwords")


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalyzerOptions:
    """分析开关与资源上限。所有上限均为非负整数。"""

    decode: bool = True
    subwords: bool = True
    max_scan_chars: int = 1_048_576
    max_decode_depth: int = 2
    max_decode_attempts: int = 64
    max_candidate_chars: int = 65_536
    max_decoded_views: int = 16
    max_decoded_bytes: int = 262_144
    max_terms_per_token: int = 32
    max_output_items: int = 100_000
    max_notices: int = 100

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in _SWITCHES:
                if not isinstance(value, bool):
                    raise TypeError(f"{item.name} must be bool")
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{item.name} must be int")
            if value < 0:
                raise ValueError(f"{item.name} must be >= 0")


@dataclass(slots=True)
class LimitHit:
    """某项上限被触发的汇总记录，同名上限只保留一条。"""

    name: str
    value: int
    count: int
    view_id: str | None
    span: Span | None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "limit": self.value, "count": self.count}

    def to_notice(self) -> Notice:
        return Notice(
            code="LIMIT_REACHED",
            level=LEVEL_LIMIT,
            message=f"resource limit {self.name}={self.value} reached; results are incomplete",
            view_id=self.view_id,
            span=self.span,
            details={"limit": self.name, "value": self.value, "count": self.count},
        )


class Budget:
    """一次 :func:`analyze` 调用内共享的计数器。

    限制记录与普通通知分开保存，因此不会被普通通知条数上限隐藏。
    """

    __slots__ = (
        "options",
        "output_items",
        "decode_attempts",
        "decoded_views",
        "decoded_bytes",
        "_hits",
    )

    def __init__(self, options: AnalyzerOptions) -> None:
        self.options = options
        self.output_items = 0
        self.decode_attempts = 0
        self.decoded_views = 0
        self.decoded_bytes = 0
        self._hits: dict[str, LimitHit] = {}

    def record_limit(
        self, name: str, *, view_id: str | None = None, span: Span | None = None
    ) -> None:
        hit = self._hits.get(name)
        if hit is None:
            value = getattr(self.options, name)
            self._hits[name] = LimitHit(name, value, 1, view_id, span)
        else:
            hit.count += 1

    def take_output_item(self, *, view_id: str | None, span: Span) -> bool:
        if self.output_items >= self.options.max_output_items:
            self.record_limit("max_output_items", view_id=view_id, span=span)
            return False
        self.output_items += 1
        return True

    def take_decode_attempt(self, *, view_id: str | None, span: Span) -> bool:
        if self.decode_attempts >= self.options.max_decode_attempts:
            self.record_limit("max_decode_attempts", view_id=view_id, span=span)
            return False
        self.decode_attempts += 1
        return True

    def take_view(self, byte_count: int, *, view_id: str | None, span: Span) -> bool:
        if self.decoded_views >= self.options.max_decoded_views:
            self.record_limit("max_decoded_views", view_id=view_id, span=span)
            return False
        if self.decoded_bytes + byte_count > self.options.max_decoded_bytes:
            self.record_limit("max_decoded_bytes", view_id=view_id, span=span)
            return False
        self.decoded_views += 1
        self.decoded_bytes += byte_count
        return True

    @property
    def hits(self) -> list[LimitHit]:
        return list(self._hits.values())
