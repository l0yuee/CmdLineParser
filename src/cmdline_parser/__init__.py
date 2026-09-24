"""面向安全日志检索的命令行文本分析器。

对任意命令行文本做容错切分、搜索词生成与常见编码的静态解码，输出结构化结果。
核心库不执行任何代码、不访问网络，也不判断命令是否恶意。
"""

from __future__ import annotations

__version__ = "0.1.0"

from .analyzer import RULES_VERSION, analyze
from .limits import AnalyzerOptions
from .models import (
    SCHEMA_VERSION,
    AnalysisResult,
    DecodedView,
    Group,
    Notice,
    Processing,
    Symbol,
    Term,
    Token,
)

__all__ = [
    "RULES_VERSION",
    "SCHEMA_VERSION",
    "AnalysisResult",
    "AnalyzerOptions",
    "DecodedView",
    "Group",
    "Notice",
    "Processing",
    "Symbol",
    "Term",
    "Token",
    "__version__",
    "analyze",
]
