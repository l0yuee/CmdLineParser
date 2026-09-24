"""编码探测器与静态解码器。解码结果只是搜索候选，不执行代码、不访问网络。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import base64_decoder, escape_decoder, percent_decoder
from ._text import DecodeFailure, DecodeSuccess


@dataclass(frozen=True, slots=True)
class DecoderSpec:
    """一种解码器。

    ``in_place`` 解码器（百分号、转义）按原位替换编码单元、保留其余文本，
    因此整段基础片段已覆盖其中的 ``name=value`` 值与单片段引号内容。
    """

    name: str
    in_place: bool
    detect: Callable[[str, bool], bool]
    decode: Callable[[str, bool], DecodeSuccess | DecodeFailure]


DECODERS: tuple[DecoderSpec, ...] = (
    DecoderSpec("base64", False, base64_decoder.detect, base64_decoder.decode),
    DecoderSpec("percent", True, percent_decoder.detect, percent_decoder.decode),
    DecoderSpec("hex_escape", True, escape_decoder.detect_hex, escape_decoder.decode_hex),
    DecoderSpec(
        "unicode_escape", True, escape_decoder.detect_unicode, escape_decoder.decode_unicode
    ),
)

__all__ = ["DECODERS", "DecodeFailure", "DecodeSuccess", "DecoderSpec"]
