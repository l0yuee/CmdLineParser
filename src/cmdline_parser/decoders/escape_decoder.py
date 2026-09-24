"""``\\xNN`` 与 ``\\uNNNN`` 转义的探测与静态解码。

自动探测至少需要两个连续转义单元，其中最后一个单元可以因截断而不完整；
单个类似 ``C:\\x64`` 的片段不触发解码。只解码满足条件的连续单元，
其余文本（包括孤立单元）按原文保留。
"""

from __future__ import annotations

import re

from ._text import (
    UTF8,
    UTF16_UNITS,
    UTF16LE,
    DecodeFailure,
    DecodeSuccess,
    decode_text_prefix,
    is_plausible_text,
    looks_utf16le,
    status_of,
)

RULE_HEX = "D-ESC-X-001"
RULE_UNICODE = "D-ESC-U-001"
ENCODING_HEX = "hex_escape"
ENCODING_UNICODE = "unicode_escape"

_HEX_RUN_RE = re.compile(r"(?:\\x[0-9A-Fa-f]{2})+")
_HEX_TAIL_RE = re.compile(r"\\(x[0-9A-Fa-f]?)?\Z")
_HEX_UNIT = 4
_UNI_RUN_RE = re.compile(r"(?:\\u[0-9A-Fa-f]{4})+")
_UNI_TAIL_RE = re.compile(r"\\(u[0-9A-Fa-f]{0,3})?\Z")
_UNI_UNIT = 6


def _plan(
    value: str, run_re: re.Pattern[str], tail_re: re.Pattern[str], unit: int
) -> tuple[list[tuple[int, int]], int]:
    """找出可解码的连续单元，返回 ``(runs, body_end)``。

    ``body_end < len(value)`` 表示末尾有紧接在连续单元之后的不完整单元，
    该部分不计入解码文本。只有反斜杠的末尾片段证据不足，不能让单个单元
    触发解码（如 ``C:\\x64\\``），只截去已满足条件的连续单元之后的反斜杠。
    """
    tail = tail_re.search(value)
    tail_start = -1 if tail is None else tail.start()
    strong_tail = tail is not None and tail.group(1) is not None
    runs: list[tuple[int, int]] = []
    body_end = len(value)
    for match in run_re.finditer(value):
        count = (match.end() - match.start()) // unit
        adjacent = match.end() == tail_start
        if count >= 2 or (adjacent and strong_tail):
            runs.append((match.start(), match.end()))
            if adjacent:
                body_end = tail_start
    return runs, body_end


def detect_hex(value: str, hint: bool = False) -> bool:
    if "\\x" not in value:
        return False
    return bool(_plan(value, _HEX_RUN_RE, _HEX_TAIL_RE, _HEX_UNIT)[0])


def decode_hex(value: str, hint: bool = False) -> DecodeSuccess | DecodeFailure:
    runs, body_end = _plan(value, _HEX_RUN_RE, _HEX_TAIL_RE, _HEX_UNIT)
    if not runs:
        return DecodeFailure("no_escape_run", ENCODING_HEX, RULE_HEX, False)
    data = bytearray()
    pos = 0
    try:
        for start, end in runs:
            data += value[pos:start].encode("utf-8")
            data += bytes.fromhex(value[start:end].replace("\\x", ""))
            pos = end
        data += value[pos:body_end].encode("utf-8")
    except UnicodeEncodeError:
        return DecodeFailure("unencodable_input", ENCODING_HEX, RULE_HEX, False)

    charsets = [UTF8]
    # 整个候选都是转义字节且呈交替 NUL 特征时，再尝试 UTF-16LE。
    if runs == [(0, body_end)] and looks_utf16le(bytes(data)):
        charsets.append(UTF16LE)
    # 只有全部尝试都仅因截断而没有文本时才静默；出现非法序列或非文本时报告。
    truncated = True
    for charset in charsets:
        decoded = decode_text_prefix(bytes(data), charset)
        if decoded is None:
            truncated = False
            continue
        text, complete = decoded
        complete = complete and body_end == len(value)
        if not text and not complete:
            continue
        if not is_plausible_text(text, min_chars=1):
            truncated = False
            continue
        return DecodeSuccess(
            text=text,
            status=status_of(complete),
            encoding=ENCODING_HEX,
            charset=charset,
            rule=RULE_HEX,
            byte_count=len(data),
        )
    if truncated:
        return DecodeFailure("truncated", ENCODING_HEX, RULE_HEX, False)
    return DecodeFailure("not_text", ENCODING_HEX, RULE_HEX, True)


def _combine_units(units: list[int]) -> tuple[str, bool] | None:
    """组合 UTF-16 代码单元。返回 ``(text, dangling_high)``；孤立代理项返回 None。"""
    chars: list[str] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if 0xD800 <= unit <= 0xDBFF:
            if index + 1 == len(units):
                return "".join(chars), True
            low = units[index + 1]
            if not 0xDC00 <= low <= 0xDFFF:
                return None
            chars.append(chr(0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00)))
            index += 2
            continue
        if 0xDC00 <= unit <= 0xDFFF:
            return None
        chars.append(chr(unit))
        index += 1
    return "".join(chars), False


def detect_unicode(value: str, hint: bool = False) -> bool:
    if "\\u" not in value:
        return False
    return bool(_plan(value, _UNI_RUN_RE, _UNI_TAIL_RE, _UNI_UNIT)[0])


def decode_unicode(value: str, hint: bool = False) -> DecodeSuccess | DecodeFailure:
    runs, body_end = _plan(value, _UNI_RUN_RE, _UNI_TAIL_RE, _UNI_UNIT)
    if not runs:
        return DecodeFailure("no_escape_run", ENCODING_UNICODE, RULE_UNICODE, False)
    parts: list[str] = []
    pos = 0
    complete = body_end == len(value)
    for start, end in runs:
        parts.append(value[pos:start])
        units = [int(value[i + 2 : i + 6], 16) for i in range(start, end, _UNI_UNIT)]
        combined = _combine_units(units)
        if combined is None:
            return DecodeFailure("lone_surrogate", ENCODING_UNICODE, RULE_UNICODE, True)
        text, dangling = combined
        parts.append(text)
        pos = end
        if dangling:
            # 高代理项位于已观察载荷末尾时，低代理项可能被截断；否则为异常单元。
            if end != body_end:
                return DecodeFailure("lone_surrogate", ENCODING_UNICODE, RULE_UNICODE, True)
            complete = False
    parts.append(value[pos:body_end])
    text = "".join(parts)
    if not text and not complete:
        return DecodeFailure("truncated", ENCODING_UNICODE, RULE_UNICODE, False)
    if not is_plausible_text(text, min_chars=1):
        return DecodeFailure("not_text", ENCODING_UNICODE, RULE_UNICODE, True)
    return DecodeSuccess(
        text=text,
        status=status_of(complete),
        encoding=ENCODING_UNICODE,
        charset=UTF16_UNITS,
        rule=RULE_UNICODE,
        byte_count=len(text.encode("utf-8", "surrogatepass")),
    )
