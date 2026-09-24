"""Base64 / Base64URL 探测与严格解码。

只接受完全由单一字母表组成的候选，不过滤非法字符；只消费已有的完整
4 字符编码组，不猜测缺失字符。
"""

from __future__ import annotations

import binascii
import re
import string
from dataclasses import dataclass

from ._text import (
    UTF8,
    UTF16LE,
    DecodeFailure,
    DecodeSuccess,
    decode_text_prefix,
    is_plausible_text,
    looks_utf16le,
    status_of,
)

RULE_BASE64 = "D-B64-001"
RULE_BASE64URL = "D-B64URL-001"
ENCODING_BASE64 = "base64"
ENCODING_BASE64URL = "base64url"

MIN_PADDED_CHARS = 8
MIN_UNPADDED_CHARS = 16
MIN_TEXT_CHARS = 4
MIN_HINTED_CHARS = 4

_SHAPE_RE = re.compile(r"([A-Za-z0-9+/_-]+)(={0,2})")
_HEX_RE = re.compile(r"[0-9A-Fa-f]+")
_URL_TO_STANDARD = str.maketrans("-_", "+/")
_STANDARD_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits + "+/"


@dataclass(frozen=True, slots=True)
class _Shape:
    body: str
    padding: str
    urlsafe: bool


def _shape(value: str) -> _Shape | None:
    match = _SHAPE_RE.fullmatch(value)
    if match is None:
        return None
    body, padding = match.groups()
    standard = "+" in body or "/" in body
    urlsafe = "-" in body or "_" in body
    if standard and urlsafe:
        return None
    if padding and len(value) % 4:
        return None
    return _Shape(body, padding, urlsafe)


def _passes_general_filter(value: str, shape: _Shape) -> bool:
    minimum = MIN_PADDED_CHARS if shape.padding else MIN_UNPADDED_CHARS
    if len(value) < minimum:
        return False
    # 裸十六进制标识符、哈希值不做 Base64 解码。
    if _HEX_RE.fullmatch(shape.body):
        return False
    # 文本的 Base64 编码几乎总是同时含有大小写字母；全小写的选项名、路径等不是候选。
    has_upper = any("A" <= char <= "Z" for char in shape.body)
    has_lower = any("a" <= char <= "z" for char in shape.body)
    return has_upper and has_lower


def _unused_bits(usable: str) -> int:
    """带 padding 的末组中未使用的低位（标准字母表）；规范编码要求为 0。"""
    if usable.endswith("=="):
        return _STANDARD_ALPHABET.index(usable[-3]) & 0x0F
    if usable.endswith("="):
        return _STANDARD_ALPHABET.index(usable[-2]) & 0x03
    return 0


def detect(value: str, hint: bool = False) -> bool:
    shape = _shape(value)
    if shape is None:
        return False
    if hint and len(value) >= MIN_HINTED_CHARS:
        return True
    return _passes_general_filter(value, shape)


def decode(value: str, hint: bool = False) -> DecodeSuccess | DecodeFailure:
    shape = _shape(value)
    if shape is None:
        return DecodeFailure("not_base64", ENCODING_BASE64, RULE_BASE64, hint)
    encoding = ENCODING_BASE64URL if shape.urlsafe else ENCODING_BASE64
    rule = RULE_BASE64URL if shape.urlsafe else RULE_BASE64

    if shape.padding:
        usable = shape.body + shape.padding
        truncated = False
    else:
        cut = len(shape.body) - len(shape.body) % 4
        usable = shape.body[:cut]
        truncated = cut != len(shape.body)
    if not usable:
        return DecodeFailure("no_complete_group", encoding, rule, hint)
    if shape.urlsafe:
        usable = usable.translate(_URL_TO_STANDARD)
    # 末组的未用位非零属于非规范编码，拒绝。在严格解码之前判断，
    # 使失败原因不依赖解释器版本对未用位的处理。
    if _unused_bits(usable):
        return DecodeFailure("non_canonical", encoding, rule, hint)
    try:
        data = binascii.a2b_base64(usable, strict_mode=True)
    except binascii.Error:
        return DecodeFailure("invalid_base64", encoding, rule, hint)

    attempts: list[tuple[str, int]] = []
    if hint:
        # EncodedCommand 提示：额外尝试 UTF-16LE，并允许短载荷。
        attempts.append((UTF16LE, 1))
    if _passes_general_filter(value, shape):
        attempts.append((UTF8, MIN_TEXT_CHARS))
        if not hint and looks_utf16le(data):
            attempts.append((UTF16LE, MIN_TEXT_CHARS))

    # 只有全部尝试都仅因截断而没有文本时才静默；出现非法序列或非文本时按提示报告。
    truncated_only = bool(attempts)
    for charset, min_chars in attempts:
        decoded = decode_text_prefix(data, charset)
        if decoded is None:
            truncated_only = False
            continue
        text, complete = decoded
        complete = complete and not truncated
        if not text and not complete:
            continue
        if not is_plausible_text(text, min_chars=min_chars):
            truncated_only = False
            continue
        return DecodeSuccess(
            text=text,
            status=status_of(complete),
            encoding=encoding,
            charset=charset,
            rule=rule,
            byte_count=len(data),
        )
    if truncated_only:
        return DecodeFailure("truncated", encoding, rule, False)
    return DecodeFailure("not_text", encoding, rule, hint)
