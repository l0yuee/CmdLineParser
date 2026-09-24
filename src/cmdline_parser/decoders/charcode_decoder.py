"""十进制字符码序列探测与静态还原。

只做文本变换：按序提取十进制码元并转为字符，不解析分隔符的含义、
不执行代码、不访问网络。分隔符是什么并不重要——混淆的本质是"用非数字
字符隔开数字"，还原时只需按序取出数字。

还原结果按证据强度分级（``confidence``），不丢弃低置信结果，
由调用方按需过滤。
"""

from __future__ import annotations

import re

from ..models import STATUS_COMPLETE
from ._text import DecodeFailure, DecodeSuccess, is_plausible_text

RULE_CHARCODE = "D-CHARCODE-001"
ENCODING_CHARCODE = "charcode-decimal"
CHARSET_CHARCODE = "ascii-decimal"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# 码元数量下限。良性的 MAC 地址（2 段）、索引切片（3 段）、版本号（4 段）
# 都落在门槛之下；首版取值基于合成样例，未在真实日志上评估。
MIN_UNITS = 8
MAX_UNIT = 255
MIN_DISTINCT_SEPARATORS = 2

# 等差数列判定：良性计数器（100,101,102...）的公差很小且高度一致。
COUNTER_MAX_STEP = 2
COUNTER_RATIO = 0.8

_DIGITS_RE = re.compile(r"\d+")
_NON_DIGIT_RE = re.compile(r"[^\d]")
# 整个候选必须是"码元 + 分隔符"的交替形态，两端为码元。
_SHAPE_RE = re.compile(r"\d{1,3}(?:[^\d]+\d{1,3})+")

# 区域形态 A：片段内，码元之间恰好一个非空白分隔符（``91p78{101g116``）。
_REGION_IN_TOKEN_RE = re.compile(r"(?<!\d)\d{1,3}(?:[^\s\d]\d{1,3})+")
# 区域形态 B：跨片段，码元之间为空白（``112 111 119``）。形态 A 对此完全盲视，
# 而空白分隔的字符码数组是 PowerShell 的常见写法。
_REGION_CROSS_TOKEN_RE = re.compile(r"(?<![\w.])\d{1,3}(?:[ \t]+\d{1,3})+(?![\w.])")

# C-HINT-CHARCODE-001：区域附近出现这些文本特征时提升置信度。
EVIDENCE_TERMS = ("-split", "-join", "[char", "[int", "char[]", "foreach", "%{", "chr(")
# 分隔符字面量串（``-SPliT ':' -spLiT';' ...``）会把证据词推离区域末尾。
EVIDENCE_WINDOW = 64


def shape_matches(value: str) -> bool:
    """候选整体是否为字符码序列形态。用于廉价预筛，避免无谓的上下文扫描。"""
    return _SHAPE_RE.fullmatch(value) is not None


def iter_regions(text: str, end: int) -> list[tuple[int, int]]:
    """按位置顺序返回互不重叠的字符码区域 ``(start, stop)``。

    区域独立于基础片段边界，因此载荷中用作分隔符的 ``{``、``}``、``;`` 等
    结构符不会把码串切碎。两种形态重叠时保留先出现、较长的一个。
    """
    spans = [
        match.span()
        for pattern in (_REGION_IN_TOKEN_RE, _REGION_CROSS_TOKEN_RE)
        for match in pattern.finditer(text, 0, end)
    ]
    spans.sort(key=lambda span: (span[0], -span[1]))
    kept: list[tuple[int, int]] = []
    for start, stop in spans:
        if kept and start < kept[-1][1]:
            continue
        kept.append((start, stop))
    return kept


def evidence_at(text: str, span: tuple[int, int]) -> tuple[str, ...]:
    """区域前后窗口内出现的证据词（C-HINT-CHARCODE-001）。"""
    lower = max(0, span[0] - EVIDENCE_WINDOW)
    upper = min(len(text), span[1] + EVIDENCE_WINDOW)
    context = text[lower:upper].casefold()
    return tuple(term for term in EVIDENCE_TERMS if term in context)


def _is_counter(units: list[int]) -> bool:
    """码元是否构成等差数列，例如 ``100,101,102`` 或 ``2,4,6,8``。"""
    if len(units) < 3:
        return False
    steps = [later - earlier for earlier, later in zip(units, units[1:])]
    if any(abs(step) > COUNTER_MAX_STEP for step in steps):
        return False
    common = max(set(steps), key=steps.count)
    return steps.count(common) >= COUNTER_RATIO * len(steps)


def _script_like(text: str) -> bool:
    """还原文本是否呈命令、脚本形态：含空白或含非字母数字的标点。"""
    return any(char.isspace() for char in text) or any(
        not (char.isalnum() or char.isspace()) for char in text
    )


def classify(value: str, hint: bool = False) -> tuple[str | None, str, str]:
    """返回 ``(confidence, reason, text)``；``confidence`` 为 None 表示拒绝。

    判定顺序固定，先命中先定级：**证据优先于形态**。带上下文证据的候选
    即使还原出的是一个普通单词，也应定为 high；反之形态像脚本但无证据时
    只能定为 medium。
    """
    if not shape_matches(value):
        return None, "not_charcode_shape", ""
    units = [int(match.group()) for match in _DIGITS_RE.finditer(value)]
    if len(units) < MIN_UNITS:
        return None, "too_few_units", ""
    if any(unit > MAX_UNIT for unit in units):
        return None, "unit_out_of_range", ""
    text = "".join(chr(unit) for unit in units)
    if not is_plausible_text(text, min_chars=1):
        return None, "not_text", ""
    if not any(char.isalpha() for char in text):
        return None, "no_alpha", ""

    separators = set(_NON_DIGIT_RE.findall(value))
    if hint or len(separators) >= MIN_DISTINCT_SEPARATORS:
        return CONFIDENCE_HIGH, "", text
    if _script_like(text):
        return CONFIDENCE_MEDIUM, "", text
    if _is_counter(units):
        return None, "arithmetic_counter", ""
    return CONFIDENCE_LOW, "", text


def detect(value: str, hint: bool = False) -> bool:
    confidence, _, _ = classify(value, hint)
    return confidence is not None


def decode(value: str, hint: bool = False) -> DecodeSuccess | DecodeFailure:
    confidence, reason, text = classify(value, hint)
    if confidence is None:
        # 无证据的候选静默丢弃：良性数字串在日志中极其常见。
        return DecodeFailure(reason, ENCODING_CHARCODE, RULE_CHARCODE, hint)
    return DecodeSuccess(
        text=text,
        status=STATUS_COMPLETE,
        encoding=ENCODING_CHARCODE,
        charset=CHARSET_CHARCODE,
        rule=RULE_CHARCODE,
        byte_count=len(text.encode("utf-8")),
        confidence=confidence,
    )
