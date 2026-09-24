"""编码探测器与静态解码器。解码结果只是搜索候选，不执行代码、不访问网络。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import (
    base64_decoder,
    charcode_decoder,
    concat_decoder,
    escape_decoder,
    percent_decoder,
)
from ._text import DecodeFailure, DecodeSuccess

# 候选提示规则。提示只是文本特征，不代表确认了执行环境。
HINT_ENCODED_COMMAND = "C-HINT-ENC-001"
HINT_CHARCODE = "C-HINT-CHARCODE-001"


@dataclass(frozen=True, slots=True)
class DecoderSpec:
    """一种解码器。

    ``in_place`` 解码器（百分号、转义、拼接）按原位替换编码单元、保留其余文本，
    因此整段基础片段已覆盖其中的 ``name=value`` 值与单片段引号内容。

    ``hint_rule`` 声明本解码器消费哪一种候选提示；候选携带其他提示时按无提示
    处理，避免 ``-EncodedCommand`` 的提示影响字符码分级，反之亦然。
    """

    name: str
    in_place: bool
    detect: Callable[[str, bool], bool]
    decode: Callable[[str, bool], DecodeSuccess | DecodeFailure]
    hint_rule: str | None = None


DECODERS: tuple[DecoderSpec, ...] = (
    DecoderSpec(
        "base64",
        False,
        base64_decoder.detect,
        base64_decoder.decode,
        HINT_ENCODED_COMMAND,
    ),
    DecoderSpec(
        "charcode",
        False,
        charcode_decoder.detect,
        charcode_decoder.decode,
        HINT_CHARCODE,
    ),
    DecoderSpec("concat", True, concat_decoder.detect, concat_decoder.decode),
    DecoderSpec("percent", True, percent_decoder.detect, percent_decoder.decode),
    DecoderSpec("hex_escape", True, escape_decoder.detect_hex, escape_decoder.decode_hex),
    DecoderSpec(
        "unicode_escape", True, escape_decoder.detect_unicode, escape_decoder.decode_unicode
    ),
)

__all__ = [
    "DECODERS",
    "HINT_CHARCODE",
    "HINT_ENCODED_COMMAND",
    "DecodeFailure",
    "DecodeSuccess",
    "DecoderSpec",
]
