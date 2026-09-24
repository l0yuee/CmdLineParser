# 输出格式（schema_version = "1"）

本文档说明 `analyze()` 返回的 `AnalysisResult.to_dict()` 以及 CLI 输出的 JSON 结构。
规则 ID 的含义见 [RULES.md](RULES.md)。

所有自动探测与解码结果都是**搜索候选**：它们描述"这段文本可以被这样解读"，
不代表原命令确实被这样执行，也不代表解码出的内容真实存在于执行环境中。
分析器不执行代码、不访问网络，不补入连接符、变量值、缺失字符或执行结果。

## 1. 坐标体系

- 所有位置都是 **Python 字符串索引（Unicode 码点）**，不是 UTF-8 字节偏移。
  例如 `echo 😀 done` 中 `😀` 的区间是 `[5, 6]`。
- 区间一律为左闭右开 `[start, end)`，JSON 中是两个整数组成的列表。
- 根层（`tokens`、`groups`、`symbols` 及 `parent_view_id` 为 `null` 的视图的 `source_span`）
  使用输入原文坐标，满足 `raw[start:end] == 片段原文`。
- 每个解码视图内部的 `tokens`、`groups`、`symbols` 使用**该视图 `text` 自己的坐标**。
  视图通过 `parent_view_id` 与 `source_span` 追溯父层，逐层回到原文。
- 视图与父层之间只有整段区间映射（`mapping: "range"`），不声明逐字符对应关系。

## 2. 顶层字段

| 字段 | 类型 | 内容 |
|---|---|---|
| `schema_version` | string | 首版固定为 `"1"` |
| `raw` | string | 完整输入原文（即使只扫描了其中一部分） |
| `tokens` | array | 根层基础词项，见第 3 节 |
| `groups` | array | 根层引号分组，见第 4 节 |
| `symbols` | array | 根层边界符号，见第 5 节 |
| `decoded_views` | array | 解码视图，按生成顺序排列，见第 6 节 |
| `notices` | array | 通知：普通通知在前，资源限制通知在后，见第 7 节 |
| `processing` | object | 扫描范围、规则版本、资源使用与受限情况，见第 8 节 |

对象中的键顺序固定；数组顺序由规则决定，相同输入、相同选项、相同规则版本的输出完全一致。

## 3. Token 与搜索词项

```json
{
  "raw": "ne^tstat",
  "span": [0, 8],
  "position": 0,
  "terms": [
    {"value": "ne^tstat", "kind": "original", "rules": ["N-BASE-001", "N-CASEFOLD-001"]},
    {"value": "netstat", "kind": "alias", "rules": ["N-DEOBF-001", "N-CASEFOLD-001", "N-SUBWORD-001"]},
    {"value": "ne", "kind": "subword", "rules": ["N-SUBWORD-001", "N-CASEFOLD-001"]},
    {"value": "tstat", "kind": "subword", "rules": ["N-SUBWORD-001", "N-CASEFOLD-001"]}
  ]
}
```

| 字段 | 内容 |
|---|---|
| `raw` | 原文片段，保留引号、caret 等全部字符；`raw == text[span[0]:span[1]]` |
| `span` | 片段区间 |
| `position` | 本层基础词项的顺序号，从 0 开始连续编号；符号不占用编号 |
| `terms` | 搜索词项列表，可能为空（例如 `""`、`''` 这类只有引号的片段） |

搜索词项：

| 字段 | 内容 |
|---|---|
| `value` | 非空搜索词 |
| `kind` | `original`、`casefold`、`alias`、`subword` 之一 |
| `rules` | 产生该值的全部规则 ID，按首次贡献顺序排列，不重复 |

- 同一 Token 内相同 `value` 只出现一次：`kind` 取首个来源，`rules` 合并所有来源。
  因此 `netstat.exe` 的原始词同时带有 `N-BASE-001` 和 `N-CASEFOLD-001`，表示折叠后值不变。
- 输出顺序固定为 `original` → `casefold` → `alias` → `subword`（按 `kind` 分段，段内按生成顺序）。
- 同一 Token 的所有词项共享该 Token 的 `span` 与 `position`，别名不是额外的顺序词，
  不能用于推断词序或相邻关系。
- `original` 的值是去除两端连续 ASCII 引号后的基础词，不一定等于 `raw`。

## 4. 引号分组

```json
{"raw": "\"C:\\Program Files\\Exam", "span": [5, 27], "quote": "\"",
 "content": "C:\\Program Files\\Exam", "content_span": [6, 27], "closed": false}
```

| 字段 | 内容 |
|---|---|
| `raw` / `span` | 从开引号到闭引号（含）；未闭合时延伸到扫描末尾 |
| `quote` | `"` 或 `'` |
| `content` / `content_span` | 引号之间的内容，可以为空串 |
| `closed` | 是否找到同类闭引号 |

分组与基础扫描相互独立：分组不改变 Token 的切分。分组只按同类 ASCII 引号配对，
不模拟任何 Shell 的转义规则，只是启发式候选。未闭合分组会附带一条 `UNCLOSED_QUOTE` 通知。

## 5. 符号

```json
{"raw": "&&", "span": [3, 5]}
```

连续的 `| & ; < > ( ) [ ] { }` 记为一个符号片段。符号只记录原文和位置，
**不声明管道、重定向、命令分隔等执行语义**；缺失的符号不会被补出。

## 6. 解码视图

```json
{
  "id": "v1",
  "parent_view_id": null,
  "depth": 1,
  "source_span": [16, 64],
  "source_kind": "token",
  "candidate_rule": "C-TOKEN-001",
  "rule": "D-B64-001",
  "encoding": "base64",
  "charset": "utf-16le",
  "status": "complete",
  "hint": "C-HINT-ENC-001",
  "mapping": "range",
  "text": "Write-Output hello",
  "tokens": [ ... ],
  "groups": [],
  "symbols": []
}
```

| 字段 | 内容 |
|---|---|
| `id` | `v1`、`v2`……按生成顺序编号 |
| `parent_view_id` | 父视图 ID；`null` 表示直接来自原文 |
| `depth` | 解码层数，直接来自原文为 1；不超过 `max_decode_depth` |
| `source_span` | 被解码的候选在**父层文本**中的区间 |
| `source_kind` | 候选来源：`token`（基础片段，去除两端引号）、`kv_value`（`name=value` 的值）、`group`（引号内容） |
| `candidate_rule` | 候选来源规则：`C-TOKEN-001`、`C-KV-001`、`C-GROUP-001` |
| `rule` | 解码规则：`D-B64-001`、`D-B64URL-001`、`D-PCT-001`、`D-ESC-X-001`、`D-ESC-U-001` |
| `encoding` | `base64`、`base64url`、`percent`、`hex_escape`、`unicode_escape` |
| `charset` | 字节解释方式：`utf-8`、`utf-16le`；`unicode_escape` 为 `utf-16`（代码单元） |
| `status` | `complete`：已观察载荷全部解码；`partial`：末尾有不完整单元，只保留可确定的前缀 |
| `hint` | 候选紧跟在 `-EncodedCommand` / `-enc` 之后时为 `C-HINT-ENC-001`，否则为 `null` |
| `mapping` | 固定为 `range`，只有整段区间映射 |
| `text` | 解码得到的候选文本，非空 |
| `tokens` / `groups` / `symbols` | 对 `text` 重新运行相同的扫描与搜索词规则，坐标相对 `text` |

说明：

- `status` 只描述已观察载荷的处理情况，`complete` 不代表日志记录本身完整，
  `partial` 也不会补全缺失部分。
- `hint` 只是文本层面的提示，不代表确认了 PowerShell 执行环境。
- 同一父层中解码出相同文本的多个候选只保留第一个视图。
- 追溯原文：沿 `parent_view_id` 回到根层，每一层用 `source_span` 在父层文本中切片。

## 7. 通知

```json
{"code": "DECODE_PARTIAL", "level": "info",
 "message": "base64 payload ends with an incomplete unit; only the decodable prefix is kept",
 "view_id": null, "span": [5, 26],
 "details": {"decoded_view_id": "v1", "rule": "D-B64-001"}}
```

| 字段 | 内容 |
|---|---|
| `code` | 通知代码，见下表 |
| `level` | `info`、`warning`、`limit` |
| `message` | 英文说明，仅供阅读；程序应依据 `code` 与 `details` |
| `view_id` | 通知所在层：`null` 为原文，否则为视图 ID；`span` 使用该层坐标 |
| `span` | 相关区间，可能为 `null` |
| `details` | 附加信息，键随 `code` 而定 |

| code | level | 触发条件 | details |
|---|---|---|---|
| `UNCLOSED_QUOTE` | info | 引号到扫描末尾仍未闭合 | `{}` |
| `DECODE_PARTIAL` | info | 解码视图的 `status` 为 `partial`；`view_id`/`span` 指向候选所在的父层 | `decoded_view_id`、`rule` |
| `DECODE_FAILED` | warning | 值得报告的候选解码失败（见 RULES.md 各解码规则的"失败报告"） | `rule`、`reason` |
| `LIMIT_REACHED` | limit | 某项资源上限被触发，结果不完整 | `limit`（选项名）、`value`（上限值）、`count`（触发次数） |

- 通知都**不是**整条记录的失败。输入不符合 Shell 语法本身不产生错误。
- 普通通知（非 `LIMIT_REACHED`）最多 `max_notices` 条，超出后记为 `max_notices` 限制。
- `LIMIT_REACHED` 每个上限只出现一条，位于列表末尾，不受 `max_notices` 影响；
  其 `view_id`/`span` 指向首次触发的位置。
- 大量常见的失败会被静默丢弃，不产生通知（例如形态像 Base64 的普通单词、`%DATE%` 这类变量引用、
  只剩被截断单元而没有任何文本的载荷），详见 RULES.md。

`DECODE_FAILED` 的 `reason` 取值：`not_base64`、`no_complete_group`、`invalid_base64`、
`non_canonical`、`not_text`、`invalid_utf8`、`lone_surrogate`。

## 8. processing

```json
{
  "rules_version": "1.0.0",
  "input_length": 29,
  "scanned_span": [0, 29],
  "scan_complete": true,
  "decode_enabled": true,
  "limited": false,
  "limits": [],
  "stats": {"tokens": 4, "groups": 1, "symbols": 1, "output_items": 6,
            "decode_attempts": 0, "decoded_views": 0, "decoded_bytes": 0}
}
```

| 字段 | 内容 |
|---|---|
| `rules_version` | 规则版本，见 RULES.md |
| `input_length` | `len(raw)`，码点数 |
| `scanned_span` | 实际扫描的原文区间，始终从 0 开始；超过 `max_scan_chars` 时只扫描前缀 |
| `scan_complete` | 是否扫描了全部原文 |
| `decode_enabled` | 是否启用自动解码（CLI `--no-decode` 为 `false`） |
| `limited` | 是否触发了任何资源上限；等价于 `limits` 非空 |
| `limits` | `[{"name", "limit", "count"}]`，与 `LIMIT_REACHED` 通知一一对应、顺序相同 |
| `stats.tokens` / `groups` / `symbols` | 根层输出数量 |
| `stats.output_items` | 全部层（含解码视图）累计输出的 Token、分组、符号数 |
| `stats.decode_attempts` | 实际执行的解码尝试次数 |
| `stats.decoded_views` | 解码视图数 |
| `stats.decoded_bytes` | 解码视图累计字节数（`unicode_escape` 按 UTF-8 长度计） |

## 9. 资源上限（`AnalyzerOptions`）

| 选项 | 默认值 | 超出时的行为 |
|---|---:|---|
| `decode` | `True` | 关闭后不生成解码视图 |
| `subwords` | `True` | 关闭后不生成 `subword` 词项 |
| `max_scan_chars` | 1,048,576 | 只扫描前缀，`scan_complete=false`；`raw` 仍为完整原文 |
| `max_decode_depth` | 2 | 更深层的候选不再解码 |
| `max_decode_attempts` | 64 | 后续候选不再尝试 |
| `max_candidate_chars` | 65,536 | 超长候选不尝试解码 |
| `max_decoded_views` | 16 | 不再生成新视图 |
| `max_decoded_bytes` | 262,144 | 会使累计字节数超限的视图不生成 |
| `max_terms_per_token` | 32 | 该 Token 的后续词项被丢弃 |
| `max_output_items` | 100,000 | 停止输出后续 Token、分组、符号（所有层共享） |
| `max_notices` | 100 | 后续普通通知被丢弃 |

所有上限都是非负整数；类型错误抛出 `TypeError`，负数抛出 `ValueError`。
上限被触发时返回已完成的结果并在 `processing.limits` 与 `LIMIT_REACHED` 中说明，
不会写成语法错误，也不会静默丢弃。

## 10. 完整示例

输入（29 个字符）：

```text
ne^tstat -ano | fi^ndstr "443
```

输出（为便于阅读做了手工排版，实际 `--pretty` 的缩进略有不同）：

```json
{
  "schema_version": "1",
  "raw": "ne^tstat -ano | fi^ndstr \"443",
  "tokens": [
    {"raw": "ne^tstat", "span": [0, 8], "position": 0, "terms": [
      {"value": "ne^tstat", "kind": "original", "rules": ["N-BASE-001", "N-CASEFOLD-001"]},
      {"value": "netstat", "kind": "alias", "rules": ["N-DEOBF-001", "N-CASEFOLD-001", "N-SUBWORD-001"]},
      {"value": "ne", "kind": "subword", "rules": ["N-SUBWORD-001", "N-CASEFOLD-001"]},
      {"value": "tstat", "kind": "subword", "rules": ["N-SUBWORD-001", "N-CASEFOLD-001"]}]},
    {"raw": "-ano", "span": [9, 13], "position": 1, "terms": [
      {"value": "-ano", "kind": "original", "rules": ["N-BASE-001", "N-CASEFOLD-001"]},
      {"value": "ano", "kind": "subword", "rules": ["N-SUBWORD-001", "N-CASEFOLD-001"]}]},
    {"raw": "fi^ndstr", "span": [16, 24], "position": 2, "terms": [
      {"value": "fi^ndstr", "kind": "original", "rules": ["N-BASE-001", "N-CASEFOLD-001"]},
      {"value": "findstr", "kind": "alias", "rules": ["N-DEOBF-001", "N-CASEFOLD-001", "N-SUBWORD-001"]},
      {"value": "fi", "kind": "subword", "rules": ["N-SUBWORD-001", "N-CASEFOLD-001"]},
      {"value": "ndstr", "kind": "subword", "rules": ["N-SUBWORD-001", "N-CASEFOLD-001"]}]},
    {"raw": "\"443", "span": [25, 29], "position": 3, "terms": [
      {"value": "443", "kind": "original", "rules": ["N-BASE-001", "N-CASEFOLD-001", "N-SUBWORD-001"]}]}
  ],
  "groups": [
    {"raw": "\"443", "span": [25, 29], "quote": "\"", "content": "443",
     "content_span": [26, 29], "closed": false}
  ],
  "symbols": [{"raw": "|", "span": [14, 15]}],
  "decoded_views": [],
  "notices": [
    {"code": "UNCLOSED_QUOTE", "level": "info",
     "message": "quote \" is not closed before the end of the scanned text",
     "view_id": null, "span": [25, 29], "details": {}}
  ],
  "processing": {
    "rules_version": "1.0.0", "input_length": 29, "scanned_span": [0, 29],
    "scan_complete": true, "decode_enabled": true, "limited": false, "limits": [],
    "stats": {"tokens": 4, "groups": 1, "symbols": 1, "output_items": 6,
              "decode_attempts": 0, "decoded_views": 0, "decoded_bytes": 0}
  }
}
```

要点：

- `|` 只作为符号记录，没有被解释为管道；删去它时四个 Token 的搜索词不变。
- `"443` 的引号未闭合，只产生 `info` 通知，后续内容仍可搜索。
- `netstat`、`findstr` 是别名（`kind: "alias"`），与原始词共享位置。

## 11. CLI 协议

- 输入：`--text TEXT`、`--file PATH` 互斥；都未指定时从 stdin 读取一条完整文本。
  文件和 stdin 按 UTF-8 解码，不去除首尾空白，UTF-8 BOM 作为原文保留。
- stdout：只输出一个 JSON 文档，末尾一个换行。默认紧凑格式，`--pretty` 缩进 2 格。
  JSON 使用 UTF-8，非 ASCII 字符不转义。
- stderr：运行错误信息，格式为 `cmdline-parser: error: ...`。
- 退出码：
  - `0`：完成分析，包括语法损坏、部分解码和资源受限。
  - `1`：运行错误——文件无法读取、输入不是合法 UTF-8、`--text` 含无法编码为 UTF-8 的字符。此时 stdout 为空。
  - `2`：参数使用错误（未知选项、`--text` 与 `--file` 同时使用等），由 argparse 报告。
- `--version` 输出 `cmdline-parser <版本> (rules <规则版本>, schema <schema_version>)`。

## 12. 版本约定

- 影响 JSON 结构（字段增删、类型或含义变化）时更新 `schema_version`。
- 影响已有输出含义（规则行为、阈值、候选范围变化）时更新 `rules_version`，
  并在 RULES.md 中记录。
