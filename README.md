# cmdline-parser

面向安全日志检索的容错命令行文本分析器。输入一条命令行文本，输出：

- **基础词项**：按空白与边界符号切分的原文片段，带码点区间与顺序号；
- **引号分组与符号**：只记录原文和位置，不赋予执行语义；
- **搜索词项**：原始词、大小写折叠、caret/反引号/引号插入的去混淆别名、子词，每个词项带来源规则；
- **解码视图**：自动探测 Base64 / Base64URL、URL 百分号编码、`\xNN`、`\uNNNN`，
  支持截断载荷的前缀恢复与有界递归，每个视图可追溯到原文区间；
- **通知与处理信息**：未闭合引号、部分解码、解码失败、资源受限，全部为结构化 JSON。

输入不符合任何 Shell 语法都**不会**导致整条记录失败。

> 所有自动探测与解码结果都是**搜索候选**，不代表原命令确实如此执行。
> 分析器只做静态文本处理：不执行代码、不访问网络，不补入连接符、变量值、缺失字符或执行结果。
> 首版规则只用合成样例验证，**未在真实日志上评估误报率或召回率**。

运行时只依赖 Python 标准库。

## 安装

需要 Python 3.11 或更高版本。本机默认解释器低于 3.11 时，请另外安装一个 3.11+ 解释器，
在项目目录创建独立的虚拟环境 `.venv`，**不要替换系统默认 Python**。

Windows（PowerShell，使用 py 启动器或指定解释器的完整路径）：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
```

Linux / macOS：

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

只使用库和 CLI 时可以省略 `[test]`；构建安装包需要 `[dev]`（额外包含 `build`）。
后文命令默认已激活 `.venv`（或使用 `.venv` 中解释器的完整路径）。

## 库调用

```python
from cmdline_parser import AnalyzerOptions, analyze

result = analyze('ne^tstat -ano | fi^ndstr "443')

for token in result.tokens:
    print(token.position, token.span, token.raw)
    for term in token.terms:
        print("   ", term.kind, term.value, term.rules)

print(result.to_json(pretty=True))          # 与 CLI 输出相同的 JSON
data = result.to_dict()                     # 或直接取字典

# 调整开关与资源上限（均为关键字参数）
options = AnalyzerOptions(decode=False, max_output_items=10_000)
result = analyze("netstat.exe -ano", options=options)
```

公共接口：`analyze(text, *, options=None) -> AnalysisResult`、`AnalyzerOptions`、
`RULES_VERSION`、`SCHEMA_VERSION`、`__version__`。`text` 必须是 `str`（空串合法），
否则抛出 `TypeError`；选项类型或取值非法时抛出 `TypeError` / `ValueError`。
命令行内容本身的任何问题都不会抛出异常。

[examples/analyze_one.py](examples/analyze_one.py) 演示如何分别消费原文词、别名和解码词，
并沿来源链回到原文：

```sh
printf '%s' 'powershell -enc VwByAGkAdABlAC0ATwB1AHQAcAB1AHQAIABoAGUAbABsAG8A' | python examples/analyze_one.py
python examples/analyze_one.py --jsonl < sample.txt   # 输出适合写入检索系统的 JSON Lines 记录
```

也可以用 `--text TEXT` 直接传入短文本。

## CLI 用法

安装后提供 `cmdline-parser` 命令，也可以使用 `python -m cmdline_parser`。
每次调用分析**一条**完整文本，stdout 只输出一个 JSON 文档。

推荐通过 stdin 或文件传入样本，避免外层 Shell 提前解释其中的引号、caret、管道符：

```sh
# Linux / macOS：单引号内的内容原样传入
printf '%s' 'ne^tstat -ano | fi^ndstr "443' | cmdline-parser --pretty

# 从文件读取（整个文件作为一条文本，按 UTF-8 解码）
cmdline-parser --file sample.txt

# 短文本也可以直接作为参数
cmdline-parser --text 'netstat.exe -ano'
```

```powershell
# Windows PowerShell：单引号字符串中的 ^ | " 不会被解释
'ne^tstat -ano | fi^ndstr "443' | cmdline-parser --pretty
```

说明：

- PowerShell 通过管道传给外部程序时会在末尾追加换行，`raw` 中会包含该换行（不影响词项）；
  需要逐字节保真时请使用 `--file`。Windows PowerShell 5.1 的管道默认编码不是 UTF-8，
  非 ASCII 样本请使用 `--file` 或 PowerShell 7。
- 输入不去除首尾空白；UTF-8 BOM 作为原文保留。

选项：

| 选项 | 作用 |
|---|---|
| `--text TEXT` | 分析参数中的文本 |
| `--file PATH` | 分析整个 UTF-8 文件；与 `--text` 互斥 |
| `--pretty` | 缩进输出 JSON |
| `--no-decode` | 关闭自动解码 |
| `--version` | 显示程序、规则与 JSON 协议版本 |

其余调优参数（资源上限、子词开关）首版只通过库接口提供。

退出码：`0` 完成分析（包括语法损坏、部分解码和资源受限）；`1` 运行错误
（文件无法读取、输入不是合法 UTF-8 等，信息写入 stderr，stdout 为空）；`2` 参数使用错误。

输出示例（`--pretty` 输出的节选，完整字段见 [docs/OUTPUT_FORMAT.md](docs/OUTPUT_FORMAT.md)）：

```json
{
  "schema_version": "2",
  "raw": "ne^tstat -ano | fi^ndstr \"443",
  "tokens": [
    {
      "raw": "ne^tstat",
      "span": [0, 8],
      "position": 0,
      "terms": [
        {"value": "ne^tstat", "kind": "original", "rules": ["N-BASE-001", "N-CASEFOLD-001"], "noise": false},
        {"value": "netstat", "kind": "alias", "rules": ["N-DEOBF-001", "N-CASEFOLD-001", "N-SUBWORD-001"], "noise": false},
        ...
```

## 文档

- [docs/OUTPUT_FORMAT.md](docs/OUTPUT_FORMAT.md)：JSON 字段、坐标体系、通知、资源上限、CLI 协议与版本约定。
- [docs/RULES.md](docs/RULES.md)：全部规则的稳定 ID、适用条件、正反例、典型误识别与变更约定。

`docs/` 只放对外文档。规划文档放在 `plans/`，只在本地保留，不纳入版本库也不进安装包。

## 运行测试

```sh
python -m pytest                              # 全部测试
python -m pytest tests/test_fixtures.py       # 只运行固定样例
HYPOTHESIS_PROFILE=ci python -m pytest        # 性质测试每项 1000 个样例（默认 200）
HYPOTHESIS_PROFILE=quick python -m pytest     # 快速运行（每项 30 个样例）
```

PowerShell 中设置环境变量：`$env:HYPOTHESIS_PROFILE = "ci"; python -m pytest`。

测试组成：

- `tests/fixtures/cases.jsonl`：人工审核的固定样例（110 余条，覆盖规划列出的 23 个类别），
  由 `tests/test_fixtures.py` 加载；编码载荷在加载时由无害文本（如 `Write-Output hello`）生成。
- `tests/test_properties.py`：基于 Hypothesis 的性质测试（原文切片、确定性、来源追溯、截断前缀稳定、
  不补全、资源上限等），公共断言在 `tests/invariants.py`。
- `tests/test_cli.py` 与 CLI 固定样例：只启动本项目 CLI 自身。
- 其余为扫描、搜索词、解码器、来源追溯、资源上限的单元测试。

核心库测试不调用命令执行与网络入口。

## 性能基准

```sh
python scripts/benchmark.py            # 完整基准（含 1 MiB 输入）
python scripts/benchmark.py --quick    # 较少重复次数
```

基准覆盖约 1 KiB、4 KiB、64 KiB、1 MiB 的普通输入以及高密度符号、高密度编码候选输入，
输出机器与解释器信息、耗时（多次运行取最小值）、`tracemalloc` 峰值内存、输出项数量与受限情况，
并检查输出有界、耗时随输入增长不出现明显的二次关系。结果只反映运行基准的那台机器，
本项目不承诺吞吐量指标。

一次参考测量（2026-09-24，Windows 10 19045，Intel64 Family 6 Model 165，16 逻辑核，
每项 3 次取最小值；完整记录用 `--json` 保存）：

| 用例 | 选项 | CPython 3.11.16 | CPython 3.14.2 | 峰值内存 |
|---|---|---|---|---|
| 普通 64 KiB | 默认 | 66 ms | 69 ms | 5.7 MiB |
| 普通 1 MiB | 放开输出上限 | 1503 ms | 1205 ms | 92 MiB |
| 高密度符号 256 KiB | 放开输出上限 | 819 ms | 738 ms | 51–53 MiB |
| 高密度编码 1 MiB | 默认（解码预算受限） | 794 ms | 708 ms | 54 MiB |

两个解释器上所有检查均通过；相邻规模（×16）的耗时增长指数在 0.98–1.13 之间（线性约为 1，二次约为 2）。
序列化也要计入：1 MiB 普通输入的完整 JSON 约 34 MiB，`to_json` 耗时与分析本身在同一量级。

## 构建安装包

```sh
python -m pip install -e ".[dev]"
python -m build          # 在 dist/ 下生成 wheel 与源码包
```

## 已知限制

详见 [docs/RULES.md](docs/RULES.md#已知限制汇总)，要点：

- 不识别任何 Shell 语法：引号、caret、反斜杠转义、变量、子命令都不求值，符号只记录位置。
- 引号分组不模拟反斜杠转义；自然语言中的撇号（`it's`）会开启分组。
- 不做 NFKC 等 Unicode 归一化（全角 `ｃｍｄ` 不映射为 `cmd`）；UTF-8 BOM 保留在第一个 Token 中。
- Base64 形态的普通单词（`--EncodedCommand`、`-ExecutionPolicy`、`SilentlyContinue` 等）会被尝试并静默失败，
  占用解码尝试预算；`-enc` 之后的片段门槛很低，可能产生无意义的候选。
- 只有 `-enc`、`-encodedcommand` 产生 EncodedCommand 提示，且只作用于下一个 Token。
- 无 padding 且长度不是 4 的倍数的 Base64 按截断处理（partial）；截断恰在 4 字符组边界时无法察觉。
- 百分号候选末尾的 `%VAR%` 可能导致 partial；只有一个 `%HH` 的失败不报告。
- 只剩被截断单元、没有任何文本的载荷静默丢弃；解码只尝试 UTF-8 与 UTF-16LE。
- 阈值基于合成样例，未经真实日志评估。

## 目录

```text
src/cmdline_parser/      库与 CLI
  analyzer.py            分析入口与有界递归解码
  scanner.py             基础扫描、符号、引号分组
  normalization.py       搜索词生成
  limits.py              选项与资源预算
  models.py              输出数据结构与 JSON 序列化
  cli.py, __main__.py    命令行入口
  decoders/              Base64、百分号、转义解码器
tests/                   pytest + Hypothesis 测试与固定样例
docs/                    输出格式、规则文档
plans/                   规划文档（本地保留，git 忽略）
examples/analyze_one.py  消费示例
scripts/benchmark.py     性能基准
```
