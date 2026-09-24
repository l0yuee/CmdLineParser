"""标记类规则（``M-*``）：只在结果里描述"看到了什么模式"，不产生词项、不产生视图。

标记与解码的界限是项目的红线：**能纯静态、确定性还原的做成解码视图；
依赖运行时值或环境的只打标记，绝不补值。** 本模块只做后者。

所有匹配都是纯文本正则，不执行代码、不访问网络、不读取环境变量。
"""

from __future__ import annotations

import re

RULE_SLICE = "M-SLICE-001"

# 常见于混淆链的自动变量与偏好变量前缀；``$env:`` 是索引切片的典型载体。
_VARIABLE_PREFIXES = ("env:", "pshome", "shellid", "profile", "pwd", "home", "host")
_VARIABLE_NAME_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
# 变量名 + 下标常量列表，下标全部为十进制字面量（``$EnV:CoMSPEC[4,26,25]``）。
# 下标里只要出现变量或表达式就不再匹配：那已经不是"静态可描述的模式"。
_SLICE_RE = re.compile(
    r"\$(?P<name>[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)?)"
    r"\[(?P<indices>\s*-?\d+(?:\s*,\s*-?\d+)+)\s*\]"
)
# 后面确实跟着拼接/取字符操作时，模式才算成立。
_FOLLOWED_BY_RE = re.compile(r"(?:-\s*join|-join|-j)\b|\[char\]", re.IGNORECASE)


def looks_like_slice_variable(name: str) -> bool:
    """变量名是否属于常见的自动变量 / 环境变量形态。"""
    folded = name.casefold()
    if folded.startswith("env:"):
        return True
    return folded in _VARIABLE_PREFIXES


def iter_slices(text: str) -> list[tuple[str, list[int], tuple[int, int]]]:
    """返回 ``(变量名, 下标列表, span)``，按出现顺序。

    只报告"对自动/环境变量做十进制下标切片、且紧跟拼接或取字符操作"的形态。
    这是 IEX 混淆链里非常稳定的信号，而且**比还原结果更耐久**——还原需要变量的
    运行期取值（随系统盘符、语言版本、WOW64 重定向而变），静态假定会得出错误结论。
    """
    found: list[tuple[str, list[int], tuple[int, int]]] = []
    for match in _SLICE_RE.finditer(text):
        name = match.group("name")
        if not looks_like_slice_variable(name):
            continue
        if not _FOLLOWED_BY_RE.search(text, match.end(), min(len(text), match.end() + 32)):
            continue
        indices = [int(part) for part in match.group("indices").split(",")]
        found.append((f"${name}", indices, match.span()))
    return found
