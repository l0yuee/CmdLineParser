"""分析入口：基础扫描、搜索词生成与有界的递归解码。

输入不符合任何 Shell 语法都不会导致整条记录失败；达到资源上限时返回
已完成的结果并记录受限信息。
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass

from . import markers, normalization, scanner
from .decoders import DECODERS, HINT_CHARCODE, HINT_ENCODED_COMMAND, DecodeFailure
from .decoders import charcode_decoder
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

RULES_VERSION = "1.1.0"

CANDIDATE_TOKEN = "C-TOKEN-001"
CANDIDATE_KV = "C-KV-001"
CANDIDATE_GROUP = "C-GROUP-001"
CANDIDATE_CHARCODE = "C-CHARCODE-001"

SOURCE_TOKEN = "token"
SOURCE_KV = "kv_value"
SOURCE_GROUP = "group"
SOURCE_CHARCODE = "charcode_region"

_ENCODED_COMMAND_FLAGS = frozenset({"-encodedcommand", "-enc"})

_QUOTE_CHARS = frozenset("'\"")

# DECODERS 中拼接解码器的名字，用于 M-OBFUS-001 的 concat_segments 计数。
DECODER_CONCAT = "concat"

# symbol_ratio 需要的最少片段数。短命令的符号占比天然偏高，不足此数时不作为触发项
# （占比本身仍然输出，只是不计入 triggered）。
_MIN_SYMBOL_RATIO_SEGMENTS = 20

# case_toggles 只统计不超过该长度的 \w+ 词。base64、十六进制、GUID 这类长词的
# 大小写交替来自编码本身而不是混淆；大小写随机化则发生在命令关键字这种短词上。
_CASE_WORD_MAX = 24
_WORD_RE = re.compile(r"\w+")


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
    """一段文本（原文或解码视图）的扫描结果。

    ``regions`` 与 ``high_regions`` 在扫描时算一次，供解码候选与噪声标记共用，
    避免同一层重复跑正则。
    """

    view_id: str | None
    depth: int
    text: str
    end: int
    tokens: list[Token]
    groups: list[Group]
    symbols: list[Symbol]
    regions: list[tuple[int, int]]
    high_regions: list[tuple[int, int]]


@dataclass(slots=True)
class _Metrics:
    """记录级混淆度量（``M-OBFUS-001``）。全部来自原始文本的确定性统计。

    ``symbol_ratio`` / ``case_toggles`` / ``quote_chars`` / ``charcode_coverage``
    只统计**原文**：它们刻画的是这条记录本身的写法。``concat_segments`` 则跨层累计，
    因为字面量拼接往往只在解码后的深层才出现（本项目样本根层为 0、深度 1 为 12）。
    """

    text_chars: int = 0
    tokens: int = 0
    symbols: int = 0
    case_toggles: int = 0
    quote_chars: int = 0
    concat_segments: int = 0
    high_region_chars: int = 0


@dataclass(slots=True)
class _Candidate:
    """一个解码候选。``hint`` 为提示规则 ID，只有声明消费该提示的解码器可见。

    ``covered`` 表示该候选整个落在某个字符码区域之内。非原位解码器按"整段编码
    单元"还原文本，区域内的片段不是独立单元，据此跳过，避免把一段码串切出来的
    碎片解成无意义短串；原位解码器不受影响。
    """

    text: str
    span: Span
    kind: str
    rule: str
    hint: str | None
    spans_boundary: bool
    evidence: tuple[str, ...] = ()
    covered: bool = False


def _is_encoded_command_flag(token: Token) -> bool:
    return any(
        term.kind != KIND_SUBWORD and term.value.casefold() in _ENCODED_COMMAND_FLAGS
        for term in token.terms
    )


def _count_case_toggles(text: str) -> int:
    """短词内部相邻字母之间的大小写翻转次数（``-SPliT``、``foreacH`` 各贡献若干次）。

    只统计 **长度不超过 ``_CASE_WORD_MAX`` 的 ``\\w+`` 词**：base64、十六进制串、
    GUID、长路径这类长词的大小写交替是编码本身的性质，不是混淆迹象——
    把它们算进来会让每条带 ``-EncodedCommand`` 的普通记录都触发告警。
    大小写随机化恰恰发生在命令关键字这种短词上。
    """
    toggles = 0
    for match in _WORD_RE.finditer(text):
        word = match.group()
        if len(word) > _CASE_WORD_MAX:
            continue
        previous: str | None = None
        for char in word:
            if not char.isalpha():
                continue
            if previous is not None and previous.isupper() != char.isupper():
                toggles += 1
            previous = char
    return toggles


def _high_charcode_regions(text: str, end: int) -> list[tuple[int, int]]:
    """判定为高置信字符码的区域。

    只用于 ``N-NOISE-001`` 与 ``M-OBFUS-001`` 的度量；判定走的是解码器自己的
    ``classify``，因此"高置信"的含义在解码与标记两条路径上完全一致。
    """
    high: list[tuple[int, int]] = []
    for start, stop in charcode_decoder.iter_regions(text, end):
        span = (start, stop)
        hint = bool(charcode_decoder.evidence_at(text, span))
        confidence, _, _ = charcode_decoder.classify(text[start:stop], hint)
        if confidence == charcode_decoder.CONFIDENCE_HIGH:
            high.append(span)
    return high


def _token_candidate(token: Token, *, hint: str | None) -> _Candidate | None:
    leading, trailing = normalization.edge_quote_widths(token.raw)
    start = token.span[0] + leading
    end = max(start, token.span[1] - trailing)
    value = token.raw[leading : leading + (end - start)]
    if not value:
        return None
    return _Candidate(value, (start, end), SOURCE_TOKEN, CANDIDATE_TOKEN, hint, False)


def _kv_candidate(token: Token) -> _Candidate | None:
    base = _token_candidate(token, hint=None)
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
    return _Candidate(value, (start, start + len(value)), SOURCE_KV, CANDIDATE_KV, None, False)


class _Analysis:
    def __init__(self, text: str, options: AnalyzerOptions) -> None:
        self.text = text
        self.options = options
        self.budget = Budget(options)
        self.notices: list[Notice] = []
        self.views: list[DecodedView] = []
        self.metrics = _Metrics()

    # ------------------------------------------------------------------ 输出

    def run(self) -> AnalysisResult:
        text = self.text
        options = self.options
        scan_end = min(len(text), options.max_scan_chars)
        if scan_end < len(text):
            self.budget.record_limit("max_scan_chars", span=(scan_end, len(text)))
        root = self._scan(text, scan_end, view_id=None, depth=0)
        self.metrics.tokens = len(root.tokens)
        self.metrics.symbols = len(root.symbols)
        self.metrics.text_chars = len(text)
        if options.decode:
            self._decode_from(root)
        self._mark_slices(text, scan_end)

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
            obfuscation=self._obfuscation(),
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

    def _obfuscation(self) -> dict[str, object]:
        """组装 ``processing.obfuscation``；任一度量越阈值时补一条通知。"""
        metrics = self.metrics
        options = self.options
        denominator = metrics.tokens + metrics.symbols
        symbol_ratio = round(metrics.symbols / denominator, 4) if denominator else 0.0
        coverage = (
            round(metrics.high_region_chars / metrics.text_chars, 4)
            if metrics.text_chars
            else 0.0
        )
        triggered: list[str] = []
        # 片段太少时符号占比没有统计意义：``iex ('a'+'b')`` 的占比天然就有 0.5。
        # 只有记录被切成足够多段，"符号占比高"才说明是结构符切碎而非命令短。
        if denominator >= _MIN_SYMBOL_RATIO_SEGMENTS and (
            symbol_ratio >= options.obfuscation_symbol_percent / 100
        ):
            triggered.append("symbol_ratio")
        if metrics.case_toggles >= options.obfuscation_case_toggles:
            triggered.append("case_toggles")
        if coverage >= options.obfuscation_charcode_percent / 100:
            triggered.append("charcode_coverage")
        if metrics.quote_chars >= options.obfuscation_quote_chars:
            triggered.append("quote_chars")
        if metrics.concat_segments >= options.obfuscation_concat_segments:
            triggered.append("concat_segments")

        record: dict[str, object] = {
            "symbol_ratio": symbol_ratio,
            "case_toggles": metrics.case_toggles,
            "charcode_coverage": coverage,
            "quote_chars": metrics.quote_chars,
            "concat_segments": metrics.concat_segments,
            "triggered": triggered,
        }
        if triggered:
            self._notice(
                "OBFUSCATION_SUSPECTED",
                LEVEL_WARNING,
                "record shows obfuscation indicators; metrics are descriptive, not a malware score",
                view_id=None,
                span=None,
                details={"rule": "M-OBFUS-001", **record},
            )
        return record

    def _mark_slices(self, text: str, end: int) -> None:
        """``M-SLICE-001``：对环境/自动变量的十进制下标切片只打标记。

        为什么不还原：取值需要该变量的运行期内容，属于"补入变量值"，项目明确排除。
        但"对变量做下标切片再拼接"这个**模式本身**就是很强的 IEX 混淆信号，
        而且比还原结果稳定得多。
        """
        for variable, indices, span in markers.iter_slices(text[:end]):
            self._notice(
                "OBFUSCATED_SLICE",
                LEVEL_WARNING,
                "index slice over an environment/automatic variable followed by -join",
                view_id=None,
                span=span,
                details={"rule": markers.RULE_SLICE, "variable": variable, "indices": indices},
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
        regions = charcode_decoder.iter_regions(text, end)
        high_regions = _high_charcode_regions(text, end)
        high_starts = [start for start, _ in high_regions]

        def in_high_region(span: Span) -> bool:
            """片段是否完全落在某个高置信字符码区域内。"""
            index = bisect_right(high_starts, span[0]) - 1
            if index < 0:
                return False
            start, stop = high_regions[index]
            return start <= span[0] and span[1] <= stop

        if depth == 0:
            self.metrics.quote_chars = sum(1 for char in text[:end] if char in _QUOTE_CHARS)
            self.metrics.case_toggles = _count_case_toggles(text[:end])
            self.metrics.high_region_chars = sum(
                stop - start for start, stop in high_regions
            )

        for kind, start, stop in scanner.iter_segments(text, end):
            if not budget.take_output_item(view_id=view_id, span=(start, stop)):
                break
            raw = text[start:stop]
            if kind == scanner.SYMBOL:
                symbols.append(Symbol(raw, (start, stop)))
                continue
            # N-NOISE-001：高置信混淆区内的片段不再切子词——那里的 \w+ 片段是
            # 码串被非边界分隔符切碎的残余。原始词与折叠词照常生成并标记为噪声。
            noise = options.noise_suppression and in_high_region((start, stop))
            terms, limited = normalization.build_terms(
                raw,
                subwords=options.subwords,
                max_terms=options.max_terms_per_token,
                noise=noise,
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
        return _Layer(
            view_id, depth, text, end, tokens, groups, symbols, regions, high_regions
        )

    # ------------------------------------------------------------------ 解码

    def _candidates(self, layer: _Layer) -> list[_Candidate]:
        """候选顺序：EncodedCommand 提示目标、基础片段及其 name=value 值、
        字符码区域、引号内容。

        相同文本只保留首次出现。字符码区域排在基础片段之后：区域文本跨片段时
        不受影响，与某个基础片段同文时让基础片段优先（它额外覆盖原位解码器）。
        """
        found: list[_Candidate] = []
        seen: set[str] = set()
        regions = layer.regions
        region_starts = [start for start, _ in regions]

        def covered(span: Span) -> bool:
            """候选是否整个落在某个区域内部（且不等于该区域）。

            区域互不重叠且按起点有序，二分定位即可，避免逐个比较。
            """
            index = bisect_right(region_starts, span[0]) - 1
            if index < 0:
                return False
            start, stop = regions[index]
            return start <= span[0] and span[1] <= stop and (start, stop) != span

        def add(candidate: _Candidate | None) -> None:
            if candidate is not None and candidate.text not in seen:
                seen.add(candidate.text)
                found.append(candidate)

        hinted = {
            token.position + 1 for token in layer.tokens if _is_encoded_command_flag(token)
        }
        for token in layer.tokens:
            if token.position in hinted:
                add(_token_candidate(token, hint=HINT_ENCODED_COMMAND))
        for token in layer.tokens:
            candidate = _token_candidate(token, hint=None)
            if candidate is not None:
                candidate.covered = covered(candidate.span)
            add(candidate)
            kv = _kv_candidate(token)
            if kv is not None:
                kv.covered = covered(kv.span)
            add(kv)
        for start, stop in regions:
            span = (start, stop)
            evidence = charcode_decoder.evidence_at(layer.text, span)
            add(
                _Candidate(
                    layer.text[start:stop],
                    span,
                    SOURCE_CHARCODE,
                    CANDIDATE_CHARCODE,
                    HINT_CHARCODE if evidence else None,
                    False,
                    evidence,
                )
            )
        for group in layer.groups:
            if group.content:
                add(
                    _Candidate(
                        group.content,
                        group.content_span,
                        SOURCE_GROUP,
                        CANDIDATE_GROUP,
                        None,
                        scanner.contains_boundary(group.content),
                    )
                )
        self._tag_charcode_evidence(layer, found)
        return found

    @staticmethod
    def _tag_charcode_evidence(layer: _Layer, found: list[_Candidate]) -> None:
        """为呈字符码形态的片段候选补上 C-HINT-CHARCODE-001 证据。

        基础片段自身也可能是完整码串（``112,111,119,...``），此时证据同样适用。
        先用廉价形态预筛，避免为每个片段扫描上下文。
        """
        for candidate in found:
            if candidate.evidence or not charcode_decoder.shape_matches(candidate.text):
                continue
            evidence = charcode_decoder.evidence_at(layer.text, candidate.span)
            if evidence:
                candidate.evidence = evidence
                candidate.hint = HINT_CHARCODE

    def _decode_from(self, root: _Layer) -> None:
        """按深度优先级（广度优先遍历）解码，浅层视图优先占用预算。"""
        options = self.options
        budget = self.budget
        queue: deque[_Layer] = deque([root])
        while queue:
            layer = queue.popleft()
            produced: set[str] = set()
            measured: set[Span] = set()
            for candidate in self._candidates(layer):
                for spec in DECODERS:
                    if spec.in_place and not (
                        candidate.kind == SOURCE_TOKEN
                        or (candidate.kind == SOURCE_GROUP and candidate.spans_boundary)
                    ):
                        continue
                    if candidate.covered and not spec.in_place:
                        continue
                    hint = candidate.hint is not None and candidate.hint == spec.hint_rule
                    if not spec.detect(candidate.text, hint):
                        continue
                    if spec.name == DECODER_CONCAT and candidate.span not in measured:
                        # M-OBFUS-001 的 concat_segments：本层出现过拼接迹象的连接处个数，
                        # 每处只计一次（同一处可能被多个解码器看到，但只算一次拼接）。
                        measured.add(candidate.span)
                        self.metrics.concat_segments += 1
                    where = {"view_id": layer.view_id, "span": candidate.span}
                    if len(candidate.text) > options.max_candidate_chars:
                        budget.record_limit("max_candidate_chars", **where)
                        continue
                    if layer.depth >= options.max_decode_depth:
                        budget.record_limit("max_decode_depth", **where)
                        continue
                    if not budget.take_decode_attempt(**where):
                        continue
                    outcome = spec.decode(candidate.text, hint)
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
                hint=candidate.hint,
                confidence=outcome.confidence,
                evidence=list(candidate.evidence),
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
