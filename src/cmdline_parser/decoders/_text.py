"""解码器共用的结果类型与文本识别工具。

文本解码使用增量方式：只保留可确定的前缀，不插入替代字符掩盖错误；
中间出现非法序列时整个候选失败。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

from ..models import STATUS_COMPLETE, STATUS_PARTIAL

UTF8 = "utf-8"
UTF16LE = "utf-16le"
UTF16_UNITS = "utf-16"

# 一般自动探测的文本质量门槛。
MIN_PRINTABLE_RATIO = 0.9


@dataclass(frozen=True, slots=True)
class DecodeSuccess:
    text: str
    status: str
    encoding: str
    charset: str
    rule: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class DecodeFailure:
    """候选解码失败。``notable`` 为 True 时以通知形式报告，否则静默丢弃。"""

    reason: str
    encoding: str
    rule: str
    notable: bool


def status_of(complete: bool) -> str:
    return STATUS_COMPLETE if complete else STATUS_PARTIAL


def decode_text_prefix(data: bytes, charset: str) -> tuple[str, bool] | None:
    """严格增量解码，返回 ``(text, complete)``；中间出现非法序列时返回 None。

    末尾不完整的多字节字符或代理项不计入文本，``complete`` 为 False。
    开头的字节序标记（BOM）属于编码签名，会被去除。
    """
    if charset == UTF16LE:
        if data.startswith(codecs.BOM_UTF16_LE):
            data = data[len(codecs.BOM_UTF16_LE):]
        decoder = codecs.utf_16_le_decode
    else:
        if data.startswith(codecs.BOM_UTF8):
            data = data[len(codecs.BOM_UTF8):]
        decoder = codecs.utf_8_decode
    try:
        text, consumed = decoder(data, "strict", False)
    except UnicodeDecodeError:
        return None
    return text, consumed == len(data)


def looks_utf16le(data: bytes) -> bool:
    """具有 UTF-16LE BOM，或奇数位明显交替出现 NUL。"""
    if data.startswith(codecs.BOM_UTF16_LE):
        return True
    if len(data) < 4:
        return False
    odd = data[1::2]
    even = data[0::2]
    return odd.count(0) * 2 >= len(odd) and even.count(0) * 10 <= len(even)


def is_plausible_text(text: str, *, min_chars: int) -> bool:
    """至少 ``min_chars`` 个字符、不含 NUL，且可打印字符及 Tab/CR/LF 占比不低于 90%。"""
    length = len(text)
    if length == 0 or length < min_chars or "\x00" in text:
        return False
    if text.isprintable():
        return True
    good = sum(1 for char in text if char.isprintable() or char in "\t\r\n")
    return good >= MIN_PRINTABLE_RATIO * length
