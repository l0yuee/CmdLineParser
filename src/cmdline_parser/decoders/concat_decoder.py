"""字面量拼接的静态重组，例如 ``'Am'+'siUt'+'ils'`` → ``'AmsiUtils'``。

只处理**字面量之间**的拼接：把同型引号之间的 ``+`` 连接符原位删除，
保留其余文本。不做表达式求值，不处理变量参与的拼接（``$a+'b'``）。

探测的是连接处 ``'+'`` 而不是完整的字面量串：基础片段候选会去除两端
引号（N-BASE-001），因此 ``'a'+'b'`` 与去引号后的 ``a'+'b`` 必须同样能处理。
"""

from __future__ import annotations

import re

from ..models import STATUS_COMPLETE
from ._text import DecodeFailure, DecodeSuccess, is_plausible_text

RULE_CONCAT = "D-CONCAT-001"
ENCODING_CONCAT = "concat"
CHARSET_CONCAT = "text"

# 同型引号 + 加号 + 同型引号，且两侧都不是空白或引号。
# 两侧的约束排除了孤立的 '+' 字面量（如 --sep='+'）与算术表达式（1 + 2）。
_JOIN_RE = re.compile(r"(?<=[^\s'\"])(['\"])[ \t]*\+[ \t]*\1(?=[^\s'\"])")


def detect(value: str, hint: bool = False) -> bool:
    return _JOIN_RE.search(value) is not None


def decode(value: str, hint: bool = False) -> DecodeSuccess | DecodeFailure:
    text = _JOIN_RE.sub("", value)
    if text == value:
        return DecodeFailure("no_change", ENCODING_CONCAT, RULE_CONCAT, False)
    if not is_plausible_text(text, min_chars=1):
        return DecodeFailure("not_text", ENCODING_CONCAT, RULE_CONCAT, False)
    return DecodeSuccess(
        text=text,
        status=STATUS_COMPLETE,
        encoding=ENCODING_CONCAT,
        charset=CHARSET_CONCAT,
        rule=RULE_CONCAT,
        byte_count=len(text.encode("utf-8")),
    )
