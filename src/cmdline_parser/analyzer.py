"""分析入口：基础扫描、搜索词生成与有界的递归解码。

输入不符合任何 Shell 语法都不会导致整条记录失败；达到资源上限时返回
已完成的结果并记录受限信息。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from . import normalization, scanner
from .decoders import DECODERS, DecodeFailure
from .limits import AnalyzerOptions, Budget
from .models import (
    KIND_SUBWORD,
    LEVEL_INFO,
    LEVEL_WARNING,
    STATUS_PARTIAL,
    AnalysisResult,
    DecodedView,
    Group,
    Notice,
    Processing,
    Span,
    Symbol,
    Token,
)

RULES_VERSION = "1.0.0"

CANDIDATE_TOKEN = "C-TOKEN-001"
CANDIDATE_KV = "C-KV-001"
CANDIDATE_GROUP = "C-GROUP-001"
HINT_ENCODED_COMMAND = "C-HINT-ENC-001"

SOURCE_TOKEN = "token"
SOURCE_KV = "kv_value"
SOURCE_GROUP = "group"

_ENCODED_COMMAND_FLAGS = frozenset({"-encodedcommand", "-enc"})


def analyze(text: str, *, options: AnalyzerOptions | None = None) -> AnalysisResult:
    """分析一条命令行文本。

    ``text`` 必须为 ``str``（空串合法）；其他类型属于调用错误，抛出 ``TypeError``。
    命令行内容本身的任何异常都不会抛出异常。
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, not {type(text).__name__}")
    if options is None:
        options = AnalyzerOptions()
    elif not isinstance(options, AnalyzerOptions):
        raise TypeError(f"options must be AnalyzerOptions, not {type(options).__name__}")
    return _Analysis(text, options).run()


@dataclass(slots=True)
class _Layer:
    """一段文本（原文或解码视图）的扫描结果。"""

    view_id: str | None
    depth: int
    text: str
    tokens: list[Token]
    groups: list[Group]
    symbols: list[Symbol]


@dataclass(slots=True)
class _Candidate:
    text: str
    span: Span
    kind: str
    rule: str
    hint: bool
    spans_boundary: bool


def _is_encoded_command_flag(token: Token) -> bool:
    return any(
        term.kind != KIND_SUBWORD and term.value.casefold() in _ENCODED_COMMAND_FLAGS
        for term in token.terms
    )


def _token_candidate(token: Token, *, hint: bool) -> _Candidate | None:
    leading, trailing = normalization.edge_quote_widths(token.raw)
    start = token.span[0] + leading
    end = max(start, token.span[1] - trailing)
    value = token.raw[leading : leading + (end - start)]
    if not value:
        return None
    return _Candidate(value, (start, end), SOURCE_TOKEN, CANDIDATE_TOKEN, hint, False)


def _kv_candidate(token: Token) -> _Candidate | None:
    base = _token_candidate(token, hint=False)
    if base is None:
        return None
    index = base.text.find("=")
    if index <= 0:
        return None
    raw_value = base.text[index + 1 :]
    leading, trailing = normalization.edge_quote_widths(raw_value)
    value = raw_value[leading : len(raw_value) - trailing] if len(raw_value) > leading else ""
    if not value:
        return None
    start = base.span[0] + index + 1 + leading
    return _Candidate(value, (start, start + len(value)), SOURCE_KV, CANDIDATE_KV, False, False)


class _Analysis:
    def __init__(self, text: str, options: AnalyzerOptions) -> None:
        self.text = text
        self.options = options
        self.budget = Budget(options)
        self.notices: list[Notice] = []
        self.views: list[DecodedView] = []

    # ------------------------------------------------------------------ 输出

    def run(self) -> AnalysisResult:
        text = self.text
        options = self.options
        scan_end = min(len(text), options.max_scan_chars)
        if scan_end < len(text):
            self.budget.record_limit("max_scan_chars", span=(scan_end, len(text)))
        root = self._scan(text, scan_end, view_id=None, depth=0)
        if options.decode:
            self._decode_from(root)

        hits = self.budget.hits
        processing = Processing(
            rules_version=RULES_VERSION,
            input_length=len(text),
            scanned_span=(0, scan_end),
            scan_complete=scan_end == len(text),
            decode_enabled=options.decode,
            limited=bool(hits),
            limits=[hit.to_dict() for hit in hits],
            stats={
                "tokens": len(root.tokens),
                "groups": len(root.groups),
                "symbols": len(root.symbols),
                "output_items": self.budget.output_items,
                "decode_attempts": self.budget.decode_attempts,
                "decoded_views": len(self.views),
                "decoded_bytes": self.budget.decoded_bytes,
            },
        )
        return AnalysisResult(
            raw=text,
            tokens=root.tokens,
            groups=root.groups,
            symbols=root.symbols,
            decoded_views=self.views,
            notices=self.notices + [hit.to_notice() for hit in hits],
            processing=processing,
        )

    def _notice(
        self,
        code: str,
        level: str,
        message: str,
        *,
        view_id: str | None,
        span: Span | None,
        details: dict[str, object] | None = None,
    ) -> None:
        if len(self.notices) >= self.options.max_notices:
            self.budget.record_limit("max_notices", view_id=view_id, span=span)
            return
        self.notices.append(Notice(code, level, message, view_id, span, details or {}))

    # ------------------------------------------------------------------ 扫描

    def _scan(self, text: str, end: int, *, view_id: str | None, depth: int) -> _Layer:
        options = self.options
        budget = self.budget
        tokens: list[Token] = []
        symbols: list[Symbol] = []
        groups: list[Group] = []

        for kind, start, stop in scanner.iter_segments(text, end):
            if not budget.take_output_item(view_id=view_id, span=(start, stop)):
                break
            raw = text[start:stop]
            if kind == scanner.SYMBOL:
                symbols.append(Symbol(raw, (start, stop)))
                continue
            terms, limited = normalization.build_terms(
                raw, subwords=options.subwords, max_terms=options.max_terms_per_token
            )
            if limited:
                budget.record_limit("max_terms_per_token", view_id=view_id, span=(start, stop))
            tokens.append(Token(raw, (start, stop), len(tokens), terms))

        for quote, start, stop, closed in scanner.iter_groups(text, end):
            if not budget.take_output_item(view_id=view_id, span=(start, stop)):
                break
            content_span = (start + 1, stop - 1 if closed else stop)
            groups.append(
                Group(
                    raw=text[start:stop],
                    span=(start, stop),
                    quote=quote,
                    content=text[content_span[0] : content_span[1]],
                    content_span=content_span,
                    closed=closed,
                )
            )
            if not closed:
                self._notice(
                    "UNCLOSED_QUOTE",
                    LEVEL_INFO,
                    f"quote {quote} is not closed before the end of the scanned text",
                    view_id=view_id,
                    span=(start, stop),
                )
        return _Layer(view_id, depth, text, tokens, groups, symbols)

    # ------------------------------------------------------------------ 解码

    def _candidates(self, layer: _Layer) -> list[_Candidate]:
        """候选顺序：EncodedCommand 提示目标、基础片段及其 name=value 值、引号内容。

        相同文本只保留首次出现。
        """
        found: list[_Candidate] = []
        seen: set[str] = set()

        def add(candidate: _Candidate | None) -> None:
            if candidate is not None and candidate.text not in seen:
                seen.add(candidate.text)
                found.append(candidate)

        hinted = {
            token.position + 1 for token in layer.tokens if _is_encoded_command_flag(token)
        }
        for token in layer.tokens:
            if token.position in hinted:
                add(_token_candidate(token, hint=True))
        for token in layer.tokens:
            add(_token_candidate(token, hint=False))
            add(_kv_candidate(token))
        for group in layer.groups:
            if group.content:
                add(
                    _Candidate(
                        group.content,
                        group.content_span,
                        SOURCE_GROUP,
                        CANDIDATE_GROUP,
                        False,
                        scanner.contains_boundary(group.content),
                    )
                )
        return found

    def _decode_from(self, root: _Layer) -> None:
        """按深度优先级（广度优先遍历）解码，浅层视图优先占用预算。"""
        options = self.options
        budget = self.budget
        queue: deque[_Layer] = deque([root])
        while queue:
            layer = queue.popleft()
            produced: set[str] = set()
            for candidate in self._candidates(layer):
                for spec in DECODERS:
                    if spec.in_place and not (
                        candidate.kind == SOURCE_TOKEN
                        or (candidate.kind == SOURCE_GROUP and candidate.spans_boundary)
                    ):
                        continue
                    if not spec.detect(candidate.text, candidate.hint):
                        continue
                    where = {"view_id": layer.view_id, "span": candidate.span}
                    if len(candidate.text) > options.max_candidate_chars:
                        budget.record_limit("max_candidate_chars", **where)
                        continue
                    if layer.depth >= options.max_decode_depth:
                        budget.record_limit("max_decode_depth", **where)
                        continue
                    if not budget.take_decode_attempt(**where):
                        continue
                    outcome = spec.decode(candidate.text, candidate.hint)
                    if isinstance(outcome, DecodeFailure):
                        if outcome.notable:
                            self._notice(
                                "DECODE_FAILED",
                                LEVEL_WARNING,
                                f"{outcome.encoding} candidate could not be decoded as text",
                                details={"rule": outcome.rule, "reason": outcome.reason},
                                **where,
                            )
                        continue
                    if outcome.text in produced:
                        continue
                    if not budget.take_view(outcome.byte_count, **where):
                        continue
                    produced.add(outcome.text)
                    queue.append(self._add_view(layer, candidate, outcome))

    def _add_view(self, parent: _Layer, candidate: _Candidate, outcome) -> _Layer:
        view_id = f"v{len(self.views) + 1}"
        depth = parent.depth + 1
        layer = self._scan(outcome.text, len(outcome.text), view_id=view_id, depth=depth)
        self.views.append(
            DecodedView(
                id=view_id,
                parent_view_id=parent.view_id,
                depth=depth,
                source_span=candidate.span,
                source_kind=candidate.kind,
                candidate_rule=candidate.rule,
                rule=outcome.rule,
                encoding=outcome.encoding,
                charset=outcome.charset,
                status=outcome.status,
                hint=HINT_ENCODED_COMMAND if candidate.hint else None,
                text=outcome.text,
                tokens=layer.tokens,
                groups=layer.groups,
                symbols=layer.symbols,
            )
        )
        if outcome.status == STATUS_PARTIAL:
            self._notice(
                "DECODE_PARTIAL",
                LEVEL_INFO,
                f"{outcome.encoding} payload ends with an incomplete unit; only the decodable prefix is kept",
                view_id=parent.view_id,
                span=candidate.span,
                details={"decoded_view_id": view_id, "rule": outcome.rule},
            )
        return layer
