"""搜索词生成：基础词、大小写折叠、词内插入字符别名与子词。

原始词项始终保留，所有转换只增加搜索词项。转换只服务于检索，
不表示执行语义等价。
"""

from __future__ import annotations

import re
import unicodedata

from .models import KIND_ALIAS, KIND_CASEFOLD, KIND_ORIGINAL, KIND_SUBWORD, Term

RULE_BASE = "N-BASE-001"
RULE_CASEFOLD = "N-CASEFOLD-001"
RULE_DEOBF = "N-DEOBF-001"
RULE_SUBWORD = "N-SUBWORD-001"

EDGE_QUOTES = "'\""

# 两侧均为字母、数字或下划线的连续 caret、反引号、ASCII 引号。
# 在 str 模式下 \w 等价于 str.isalnum() 或下划线。
_INSERTION_RE = re.compile(r"(?<=\w)[\^`'\"]+(?=\w)")
_WORD_RE = re.compile(r"\w+")


def edge_quote_widths(raw: str) -> tuple[int, int]:
    """返回片段两端连续 ASCII 引号的个数 ``(leading, trailing)``。"""
    rest = raw.lstrip(EDGE_QUOTES)
    leading = len(raw) - len(rest)
    trailing = len(rest) - len(rest.rstrip(EDGE_QUOTES))
    return leading, trailing


def base_term(raw: str) -> str:
    """基础搜索词：去除片段两端连续的 ASCII 单、双引号，内嵌引号保留。"""
    return raw.strip(EDGE_QUOTES)


def remove_insertions(value: str) -> str:
    """移除词内插入字符，例如 ``ne^tstat`` → ``netstat``。"""
    return _INSERTION_RE.sub("", value)


def extract_subwords(value: str) -> list[str]:
    """提取连续的 Unicode 字母、数字、下划线片段。

    组合字符（类别 M*）附着在前面的片段上，避免把带重音的字母拆成 ASCII 片段。
    """
    if value.isascii():
        return _WORD_RE.findall(value)
    parts: list[str] = []
    start = -1
    for index, char in enumerate(value):
        if char.isalnum() or char == "_":
            if start < 0:
                start = index
        elif start >= 0 and unicodedata.category(char)[0] == "M":
            continue
        elif start >= 0:
            parts.append(value[start:index])
            start = -1
    if start >= 0:
        parts.append(value[start:])
    return parts


class _TermSet:
    """按首次出现顺序合并同值词项，保留首个来源的类别并合并来源规则。"""

    __slots__ = ("_terms", "_index", "_max", "limited")

    def __init__(self, max_terms: int) -> None:
        self._terms: list[Term] = []
        self._index: dict[str, Term] = {}
        self._max = max_terms
        self.limited = False

    def add(self, value: str, kind: str, rules: tuple[str, ...]) -> None:
        if not value:
            return
        existing = self._index.get(value)
        if existing is not None:
            for rule in rules:
                if rule not in existing.rules:
                    existing.rules.append(rule)
            return
        if len(self._terms) >= self._max:
            self.limited = True
            return
        term = Term(value, kind, list(rules))
        self._terms.append(term)
        self._index[value] = term

    @property
    def terms(self) -> list[Term]:
        return self._terms


def build_terms(raw: str, *, subwords: bool = True, max_terms: int = 32) -> tuple[list[Term], bool]:
    """为一个基础片段生成搜索词项，返回 ``(terms, limited)``。

    输出顺序固定：原始词、折叠词、混淆别名（及其折叠形式）、子词。
    """
    base = base_term(raw)
    if not base:
        return [], False
    terms = _TermSet(max_terms)
    folded = base.casefold()
    terms.add(base, KIND_ORIGINAL, (RULE_BASE,))
    terms.add(folded, KIND_CASEFOLD, (RULE_CASEFOLD,))

    sources: list[tuple[str, tuple[str, ...]]] = [(base, ()), (folded, (RULE_CASEFOLD,))]
    alias = remove_insertions(base)
    if alias != base:
        alias_folded = alias.casefold()
        terms.add(alias, KIND_ALIAS, (RULE_DEOBF,))
        terms.add(alias_folded, KIND_ALIAS, (RULE_DEOBF, RULE_CASEFOLD))
        sources.append((alias, (RULE_DEOBF,)))
        sources.append((alias_folded, (RULE_DEOBF, RULE_CASEFOLD)))

    if subwords:
        for value, chain in sources:
            for part in extract_subwords(value):
                terms.add(part, KIND_SUBWORD, chain + (RULE_SUBWORD,))
    return terms.terms, terms.limited
