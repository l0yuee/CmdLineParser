"""URL 百分号编码探测与解码。

只解码合法的 ``%HH``，不把 ``+`` 转为空格；不构成 ``%HH`` 的百分号按原文保留。
候选末尾的 ``%`` 或 ``%H`` 视为可能被截断的转义，不计入文本并标记为 partial。
"""

from __future__ import annotations

import re

from ._text import (
    UTF8,
    DecodeFailure,
    DecodeSuccess,
    decode_text_prefix,
    is_plausible_text,
    status_of,
)

RULE_PERCENT = "D-PCT-001"
ENCODING_PERCENT = "percent"

_UNIT_RE = re.compile(r"%([0-9A-Fa-f]{2})")
_TAIL_RE = re.compile(r"%[0-9A-Fa-f]?\Z")


def detect(value: str, hint: bool = False) -> bool:
    return _UNIT_RE.search(value) is not None


def decode(value: str, hint: bool = False) -> DecodeSuccess | DecodeFailure:
    tail = _TAIL_RE.search(value)
    body = value if tail is None else value[: tail.start()]
    data = bytearray()
    pos = 0
    units = 0
    try:
        for match in _UNIT_RE.finditer(body):
            data += body[pos : match.start()].encode("utf-8")
            data.append(int(match.group(1), 16))
            pos = match.end()
            units += 1
        data += body[pos:].encode("utf-8")
    except UnicodeEncodeError:
        return DecodeFailure("unencodable_input", ENCODING_PERCENT, RULE_PERCENT, False)

    # 只有一个 %HH 的失败多为 %DATE%、%CD% 一类变量引用，不单独报告。
    notable = units >= 2
    decoded = decode_text_prefix(bytes(data), UTF8)
    if decoded is None:
        return DecodeFailure("invalid_utf8", ENCODING_PERCENT, RULE_PERCENT, notable)
    text, complete = decoded
    complete = complete and tail is None
    if not text and not complete:
        # 只观察到被截断的单元，没有可确定的前缀；截断是日志中的常态，不单独报告。
        return DecodeFailure("truncated", ENCODING_PERCENT, RULE_PERCENT, False)
    if not is_plausible_text(text, min_chars=1):
        return DecodeFailure("not_text", ENCODING_PERCENT, RULE_PERCENT, notable)
    return DecodeSuccess(
        text=text,
        status=status_of(complete),
        encoding=ENCODING_PERCENT,
        charset=UTF8,
        rule=RULE_PERCENT,
        byte_count=len(data),
    )
