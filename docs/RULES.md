# 规则说明（rules_version = "1.0.0"）

本文档为分析器的每条规则分配稳定 ID，记录适用条件、正反例、限制与典型误识别，
作为后续迭代与回归评审的共同依据。字段含义见 [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md)。

所有规则只服务于日志检索：

- 基础扫描不理解任何 Shell 语法，符号不带执行语义；输入不符合语法不会导致整条记录失败。
- 搜索词转换只**增加**词项，原文与原始词始终保留；别名不表示执行语义等价。
- 解码结果只是搜索候选：静态解码，不执行代码、不访问网络；
  不补入连接符、变量值、缺失字符或执行结果。
- 首版阈值基于合成样例确定，**未在真实日志上评估**，不声明误报率或召回率。

## 版本与变更约定

- 规则 ID 格式为 `<类别>-<名称>-<序号>`，一经发布不复用：
  `S` 基础扫描，`N` 搜索词，`C` 解码候选，`D` 解码器。
- 新增行为须同步新增或更新规则 ID、适用条件、正反例和测试（`tests/fixtures/cases.jsonl` 及单元测试）。
- 影响已有输出含义（同一输入在相同选项下输出变化）时更新 `rules_version`：
  - 修订号（`1.0.x`）：修正与本文档描述不符的实现缺陷；
  - 次版本号（`1.x.0`）：调整阈值、候选范围或新增规则；
  - 主版本号（`x.0.0`）：改变已有规则的基本含义。
- 影响 JSON 结构时更新 `schema_version`（见 OUTPUT_FORMAT.md）。

| 版本 | 日期 | 变化 |
|---|---|---|
| 1.0.0 | 2026-09 | 首版：本文档列出的全部规则 |

## 规则总览

| ID | 作用 |
|---|---|
| [S-BOUNDARY-001](#s-boundary-001) | 按空白与边界符号切分基础片段 |
| [S-SYMBOL-001](#s-symbol-001) | 连续边界符号记为一个符号片段 |
| [S-GROUP-001](#s-group-001) | 同类 ASCII 引号配对为分组 |
| [N-BASE-001](#n-base-001) | 基础搜索词：去除两端引号 |
| [N-CASEFOLD-001](#n-casefold-001) | Unicode 大小写折叠 |
| [N-DEOBF-001](#n-deobf-001) | 移除词内插入的 caret、反引号、引号 |
| [N-SUBWORD-001](#n-subword-001) | 提取字母、数字、下划线子词 |
| [C-TOKEN-001](#c-token-001) | 基础片段作为解码候选 |
| [C-KV-001](#c-kv-001) | `name=value` 的值作为解码候选 |
| [C-GROUP-001](#c-group-001) | 引号分组内容作为解码候选 |
| [C-HINT-ENC-001](#c-hint-enc-001) | `-EncodedCommand` / `-enc` 后的片段带提示 |
| [D-B64-001](#d-b64-001) | 标准 Base64 |
| [D-B64URL-001](#d-b64url-001) | URL 安全 Base64 |
| [D-PCT-001](#d-pct-001) | URL 百分号编码 |
| [D-ESC-X-001](#d-esc-x-001) | `\xNN` 转义 |
| [D-ESC-U-001](#d-esc-u-001) | `\uNNNN` 转义 |

规则 ID 在输出中的位置：搜索词项的 `rules`（`N-*`）、解码视图的 `candidate_rule`、`hint`、`rule`
（`C-*`、`D-*`）、通知 `details.rule`（`D-*`）。`S-*` 规则决定 `tokens`、`symbols`、`groups`
的切分，不单独出现在输出中。

---

## 基础扫描

原文与每个解码视图的文本都使用相同的扫描规则。扫描只处理前 `max_scan_chars` 个字符，
每个输出项（Token、符号、分组）占用一个 `max_output_items` 配额。

### S-BOUNDARY-001

**条件**：Unicode 空白（`str.isspace()` 为真的字符，含 Tab、换行、NBSP、全角空格 U+3000）
和边界符号 `| & ; < > ( ) [ ] { }` 是分隔符；其余连续字符组成一个基础片段（Token）。
引号、caret、反引号、反斜杠都不是分隔符，也不改变切分。

| 输入 | Token |
|---|---|
| `netstat.exe -ano` | `netstat.exe`、`-ano` |
| `dir&&whoami` | `dir`、`whoami`（`&&` 为符号） |
| `tool "C:\Program Files\Exam` | `tool`、`"C:\Program`、`Files\Exam` |
| `echo 你好　世界`（全角空格） | `echo`、`你好`、`世界` |

**反例 / 限制**：

- 不模拟转义：`echo a^|b` 切分为 `echo`、`a^`、`|`、`b`，`|` 仍记为符号，
  但符号本身不声明管道语义，也不会因 caret 被"确认"或"取消"。
- 引号内的空白同样切分：`"C:\Program Files"` 是两个 Token；完整路径请使用分组（S-GROUP-001）。
- 零宽字符（如 U+200B）和 UTF-8 BOM（U+FEFF）不是空白，会留在 Token 中。

### S-SYMBOL-001

**条件**：连续的边界符号字符合并为一个符号片段，只记录原文与位置。符号不占用 `position` 编号。

| 输入 | 符号 |
|---|---|
| `a | b` | `|` |
| `cmd 2>&1` | `>&`（`2`、`1` 为 Token） |
| `$(whoami)` | `(`、`)`（`$`、`whoami` 为 Token） |
| `a;&|b` | `;&|`（一个片段） |

**反例 / 限制**：

- URL 查询串中的 `&`、IPv6 地址的 `[`、`]`、PowerShell 的 `@{}` 都会被记为符号并切分 Token，
  例如 `http://h/?a=1&b=2` 得到 `http://h/?a=1`、`&`、`b=2`。
- 缺失的连接符不会被补出：`netstat -ano findstr 443` 与 `netstat -ano | findstr 443` 的
  Token 搜索词完全相同，只是后者多一个符号。

### S-GROUP-001

**条件**：从左到右寻找 ASCII `"` 或 `'`，与之后第一个**同类**引号配对为分组；
组内的异类引号属于内容。找不到同类闭引号时，分组延伸到扫描末尾并标记 `closed: false`，
同时产生 `UNCLOSED_QUOTE`（info）通知。分组不影响 Token 切分。

| 输入 | 分组 |
|---|---|
| `tool "" ''` | `""`（内容为空）、`''`（内容为空），均闭合 |
| `tool "C:\Program Files\Exam` | `"C:\Program Files\Exam`，未闭合 |
| `echo "a 'b' c"` | `"a 'b' c"` 一个分组，内层单引号是内容 |
| `po"wer"shell` | `"wer"` |

**反例 / 限制**：

- 不模拟任何 Shell 的转义：`"a\"b"` 中的 `\"` 结束分组，得到 `"a\"` 与未闭合的 `"`。
- 自然语言中的撇号也会开启分组：`echo it's done` 得到未闭合分组 `'s done`。
- 分组是启发式候选，不代表原命令的真实参数边界。

---

## 搜索词

每个 Token 独立生成搜索词项，所有词项共享 Token 的 `span` 与 `position`。
同值词项合并：`kind` 取首个来源，`rules` 合并全部来源。
每个 Token 最多 `max_terms_per_token` 个词项，超出部分丢弃并记为资源受限。

### N-BASE-001

**条件**：去除 Token 两端连续的 ASCII `'`、`"` 得到原始词（`kind: "original"`），
内嵌引号保留。结果为空时该 Token 没有任何词项。

| Token | 原始词 |
|---|---|
| `netstat.exe` | `netstat.exe` |
| `"C:\Program` | `C:\Program` |
| `po"wer"shell.exe` | `po"wer"shell.exe` |
| `""`、`''`、`"'` | 无词项 |

**限制**：只去除 ASCII 引号；全角引号（`＂`、`“”`）等作为普通字符保留。

### N-CASEFOLD-001

**条件**：对原始词做 `str.casefold()`，不同于原始词时增加 `casefold` 词项；
相同时只在原始词的 `rules` 中追加本规则。别名与子词同样生成折叠形式。

| 原始词 | 折叠词 |
|---|---|
| `NetStat.EXE` | `netstat.exe` |
| `Straße` | `strasse` |
| `İ`（U+0130） | `i̇`（`i` + U+0307） |

**反例 / 限制**：

- 不做 Unicode 兼容归一化（NFKC）：全角 `ｃｍｄ` 不会映射为 `cmd`，
  全角大写 `ＣＭＤ` 只折叠为全角小写 `ｃｍｄ`。
- 不做同形字符映射（如西里尔字母 `а` 与拉丁字母 `a`）。

### N-DEOBF-001

**条件**：移除**两侧都是字母、数字或下划线**（Unicode `\w`）的连续 `^`、`` ` ``、`'`、`"`，
结果不同于原始词时增加别名（`kind: "alias"`）及其折叠形式。

| 原始词 | 别名 |
|---|---|
| `ne^tstat.exe` | `netstat.exe` |
| `fi^ndstr` | `findstr` |
| `po"wer"shell.exe` | `powershell.exe` |
| ``I`nvoke-Ex`pression`` | `Invoke-Expression`（及折叠 `invoke-expression`） |
| `a^^b` | `ab` |
| `你^好` | `你好` |

**反例**（不生成别名）：

| 原始词 | 原因 |
|---|---|
| `a^`（来自 `a^|b`） | caret 右侧不是字母数字 |
| `^dir` | caret 左侧没有字符；原始词保留 `^dir`，子词 `dir` 仍可搜索 |
| `-^enc` | caret 左侧是 `-` |
| `50%^` | caret 位于末尾 |

**限制与典型误识别**：

- 别名只用于检索，不表示执行语义等价，也不补入任何字符；
  例如 `powershe` 按原样输出，与 `powershell` 的匹配交给搜索系统。
- 自然语言撇号也会被移除：`don't` 生成别名 `dont`（原始词仍保留）。
- 不处理 `%VAR:~0,1%` 切片、字符串拼接、`set` 变量替换等需要求值的混淆。

### N-SUBWORD-001

**条件**：从原始词、折叠词、别名及别名折叠形式中提取连续的 Unicode 字母、数字、下划线片段，
组合字符（类别 `M*`）附着在前一个片段上。子词不同于已有词项时增加 `subword` 词项，
`rules` 为来源链加上本规则。可通过 `AnalyzerOptions(subwords=False)` 关闭。

| 原始词 | 子词 |
|---|---|
| `C:\Windows\System32\cmd.exe` | `C`、`Windows`、`System32`、`cmd`、`exe`、`c`、`windows`、`system32` |
| `--data=abc` | `data`、`abc` |
| `\\server\share` | `server`、`share` |
| `café`（`e` + U+0301） | 组合字符不拆开，整体为一个子词 |

**限制与典型误识别**：

- 混淆词的原始形式也会产生碎片子词，例如 `ne^tstat` 产生 `ne`、`tstat`。
- 反斜杠、斜杠、连字符只作为子词分隔，原始词不做全局删除。
- 不做中文分词：连续汉字是一个子词。

---

## 解码候选

启用解码时（默认），每一层（原文或解码视图）按以下顺序收集候选，相同文本只保留首次出现：

1. 带 C-HINT-ENC-001 提示的 Token；
2. 每个 Token（C-TOKEN-001）及其 `name=value` 值（C-KV-001）；
3. 非空分组内容（C-GROUP-001）。

每个候选依次交给 Base64、百分号、`\x`、`\u` 四个解码器。
百分号与转义解码器按原位替换、保留其余文本，因此只作用于 Token 候选
和跨越多个 Token 的分组内容（内容含空白或边界符号）；Base64 作用于全部候选。

候选被探测命中后，依次检查：长度不超过 `max_candidate_chars`、所在层深度小于
`max_decode_depth`、解码尝试次数未超过 `max_decode_attempts`，然后才解码。
成功的结果还需满足 `max_decoded_views` 与 `max_decoded_bytes`，并且同一层中不与已有视图文本重复。
解码视图按广度优先处理，浅层候选优先占用预算。

### C-TOKEN-001

**条件**：Token 去除两端连续 ASCII 引号后的非空文本；`source_span` 为去除引号后的区间。

| Token | 候选 |
|---|---|
| `V3JpdGUtT3V0cHV0IGhlbGxv` | 同原文 |
| `"V3JpdGUtT3V0cHV0IGhlbGxv"` | 去除两端引号后的 24 个字符 |
| `""` | 无候选 |

### C-KV-001

**条件**：在 C-TOKEN-001 候选文本中，第一个 `=` 位于索引大于 0 处时，
其后的文本去除两端引号后作为候选（`source_kind: "kv_value"`）。

| Token | 候选 |
|---|---|
| `--data=V3JpdGUtT3V0cHV0IGhlbGxv` | `V3JpdGUtT3V0cHV0IGhlbGxv` |
| `payload="%63%6d%64"` | `%63%6d%64`（该值只交给 Base64；百分号解码作用于整个 Token，得到 `payload="cmd`） |
| `=abc`、`a=`、`a=""` | 无候选 |

**限制**：只取第一个 `=`；`name:value`、`/name:value` 形式不拆分。

### C-GROUP-001

**条件**：分组的非空内容（`content`、`content_span`），闭合与未闭合分组都适用。

| 输入 | 候选 |
|---|---|
| `echo "%65%63%68%6F Write-Output hello"` | 分组内容（含空格，百分号解码器会处理整段） |
| `tool ''` | 无候选 |

### C-HINT-ENC-001

**条件**：某 Token 的原始词、折叠词或别名（不含子词）按 casefold 等于 `-encodedcommand`
或 `-enc` 时，同一层中下一个 Token（`position + 1`，符号不占用位置）作为带提示的候选。

提示只影响 Base64 解码器（见 D-B64-001）：降低长度门槛、优先尝试 UTF-16LE、失败时报告。
视图的 `hint` 字段记录本规则 ID。提示只是文本特征，不代表确认了 PowerShell 执行环境。

| 输入 | 带提示的候选 |
|---|---|
| `powershell -enc ZABpAHIA` | `ZABpAHIA`（UTF-16LE `dir`） |
| `powershell -EncodedCommand <载荷>` | `<载荷>` |
| `powershell "-e^nc" <载荷>` | `<载荷>`（别名为 `-enc`） |

**反例 / 限制**：

- 只识别 `-encodedcommand` 与 `-enc` 两种写法；PowerShell 接受的其他前缀缩写
  （`-e`、`-ec`、`-en`、`-enco` 等）、`/enc`、`--enc`、`-EncodedCommand:<载荷>` 不产生提示。
  这些情况下足够长的载荷仍可由一般探测找到。
- 只作用于紧随其后的一个 Token；中间的符号不阻断提示（`-enc | X` 中 `X` 仍带提示）。

---

## 解码器

解码器的共同约定：

- 严格解码，不插入替代字符；中间出现非法序列时整个候选失败，只影响该候选。
- 末尾不完整的编码单元不计入文本，视图标记为 `status: "partial"`，并产生 `DECODE_PARTIAL` 通知。
- 解码结果必须是"可信文本"：非空，不含 NUL，可打印字符（含 Tab、CR、LF）占比不低于 90%。
- 失败分两类：值得报告的失败产生 `DECODE_FAILED`（warning）通知；
  常见的非编码文本、只有被截断单元而没有任何文本的载荷静默丢弃。
- 开头的 UTF-8 / UTF-16LE 字节序标记属于编码签名，从解码文本中去除。

### D-B64-001

**探测条件**（标准字母表 `A-Z a-z 0-9 + /`）：

- 整个候选由单一字母表组成，末尾可有 1–2 个 `=`；有 `=` 时总长度须为 4 的倍数；
  同时出现 `+`/`/` 与 `-`/`_` 时拒绝。
- 一般探测：有 padding 时至少 8 个字符，无 padding 时至少 16 个字符；
  不能是纯十六进制；必须同时含大写和小写字母。
- 带 C-HINT-ENC-001 提示时：形态合法且至少 4 个字符即可。

**解码**：

- 无 padding 且长度不是 4 的倍数时，只解码完整的 4 字符组，视为截断（partial）。
- 先检查末组：未用位非零的非规范编码直接拒绝（`non_canonical`，与解释器版本无关），再严格模式解码。
- 字符集尝试顺序：带提示时先 UTF-16LE（至少 1 个字符）；通过一般探测时再 UTF-8
  （至少 4 个字符）；无提示且字节呈 UTF-16LE 特征（BOM，或奇数位至少一半为 NUL、偶数位 NUL 不超过 10%）
  时再 UTF-16LE（至少 4 个字符）。第一个得到可信文本的字符集胜出。
- `byte_count` 为解码字节数。

**失败报告**：带提示的候选失败时报告（`reason` 为 `invalid_base64`、`non_canonical`、`not_text` 等）；
无提示的候选失败一律静默；仅因截断而没有文本时总是静默。

| 输入 | 结果 |
|---|---|
| `V3JpdGUtT3V0cHV0IGhlbGxv` | UTF-8 `Write-Output hello`，complete |
| `VwByAGkAdABlAC0ATwB1AHQAcAB1AHQAIABoAGUAbABsAG8A` | UTF-16LE `Write-Output hello`（有无提示均可） |
| `powershell -enc ZABpAHIA` | 带提示，UTF-16LE `dir` |
| `V3JpdGUtT3V0cHV0IGhlb`（截去末尾 3 个字符） | `Write-Output he`，partial |
| `SGVsbG8=` | `Hello`（有 padding，8 个字符） |

**反例**（不探测或不产生视图）：

| 输入 | 原因 |
|---|---|
| `d41d8cd98f00b204e9800998ecf8427e` | 纯十六进制（哈希值） |
| `averylongidentifiername` | 只有小写字母 |
| `SGVsbG8` | 无 padding 且不足 16 个字符 |
| `SGVsbG8==` | padding 后长度不是 4 的倍数 |
| `SGVsbG9=` | 非规范编码 |
| 二进制数据的 Base64 | 解码结果不是可信文本，静默丢弃 |

**限制与典型误识别**：

- 字符集与长度符合条件的普通单词、选项名、路径会被尝试解码，例如 `--EncodedCommand`、
  `-ExecutionPolicy`、`/usr/local/bin/myScript`、`SilentlyContinue`。它们通常解码为非文本并被静默丢弃，
  但会占用 `max_decode_attempts` 配额；极少数情况下可能恰好得到可打印文本，形成误报候选。
- 带提示时门槛很低：`-enc` 之后任意 4 个字符以上的 Base64 形态片段都会按 UTF-16LE 尝试，
  可能得到无意义的 CJK 字符候选。
- 无 padding 的编码（常见于 Base64URL、JWT）长度不是 4 的倍数时，与截断无法区分，
  末尾 1–2 个字节不解码，视图标记为 partial。
- 截断恰好落在 4 字符组边界时无法察觉，视图标记为 complete（`complete` 只表示已观察载荷全部解码）。
- 不过滤、不跳过非法字符：候选中夹杂空白、引号或其他字符时整体不探测。
- 只尝试 UTF-8 与 UTF-16LE，不尝试 GBK、UTF-16BE 等其他字符集。

### D-B64URL-001

**条件**：与 D-B64-001 相同，但字母表为 `A-Z a-z 0-9 - _`，且至少出现一个 `-` 或 `_`
（只含 `A-Z a-z 0-9` 的候选按标准 Base64 记录）。解码前把 `-`、`_` 转换为 `+`、`/`。

| 输入 | 结果 |
|---|---|
| `V3JpdGUtT3V0cHV0IGhlbGxvPz4-` | `Write-Output hello?>>`，`encoding: "base64url"` |

**反例**：`SGVsbG8+/_-SGVsbG8gV29y`（混用两种字母表）不探测。限制同 D-B64-001。

### D-PCT-001

**探测条件**：候选中至少有一个合法的 `%HH`（`H` 为十六进制数字）。

**解码**：

- 每个 `%HH` 替换为对应字节，其余字符按 UTF-8 编码保留；`+` **不**转换为空格；
  不构成 `%HH` 的 `%` 原样保留。
- 候选末尾的 `%` 或 `%H` 视为被截断的单元，不计入文本，视图标记为 partial。
- 结果按 UTF-8 严格解码；末尾不完整的多字节字符不计入文本（partial）。

**失败报告**：包含至少两个 `%HH` 单元的候选解码失败（`invalid_utf8`、`not_text`）时报告；
只有一个单元时静默（多为 `%DATE%`、`%CD%` 一类变量引用）；只有被截断单元时静默。

| 输入 | 结果 |
|---|---|
| `%63%6d%64` | `cmd` |
| `a+b%20c` | `a+b c` |
| `%E4%BD%A0%E5%A5%BD` | `你好` |
| `100%zz%41` | `100%zzA` |
| `%63%6d%6` | `cm`，partial |
| `%E4%BD%A0%E5%A5` | `你`，partial |

**反例**：

| 输入 | 结果 |
|---|---|
| `100%`、`%A`、`%zz`、`%PATH%` | 不探测 |
| `%DATE%` | 探测命中（`%DA`），UTF-8 非法，静默丢弃 |
| `%E4%BD` | 只有被截断的多字节字符，静默丢弃 |
| `ab%FFcd%41` | 非法 UTF-8，报告 `DECODE_FAILED`，不拼接部分结果 |

**限制与典型误识别**：

- 末尾是变量引用的候选会被标记为 partial，例如 `%63%6d%64%TEMP%` 得到 `cmd%TEMP`（partial），
  因为末尾的 `%` 与截断单元无法区分。
- 只有一个 `%HH` 且解码失败时不报告，可能掩盖真实的编码错误。
- 只按 UTF-8 解释字节。

### D-ESC-X-001

**探测条件**：候选中存在至少两个连续的 `\xHH` 单元；或一个单元紧接在候选末尾的
不完整单元（`\x`、`\xH`）之前。只有反斜杠的末尾片段证据不足，不能让单个单元触发。

**解码**：满足条件的连续单元替换为字节，其余文本（包括孤立的单个单元）按原文保留；
紧接在连续单元之后的末尾不完整单元（含孤立反斜杠）不计入文本，视图标记为 partial。
字符集先尝试 UTF-8；整个候选都是转义单元且呈 UTF-16LE 特征时再尝试 UTF-16LE。

**失败报告**：得到非法序列或非文本时报告（`reason: "not_text"`）；只有被截断单元时静默。

| 输入 | 结果 |
|---|---|
| `\x63\x6d\x64` | `cmd` |
| `run:\x63\x6d\x64.exe` | `run:cmd.exe` |
| `\xe4\xbd\xa0\xe5\xa5\xbd` | `你好` |
| `\x63\x00\x6d\x00\x64\x00` | UTF-16LE `cmd` |
| `\x63\x6d\x6`、`\x63\x6d\` | `cm`，partial |
| `C:\x64\bin\x41\x42` | `C:\x64\binAB`（单个 `\x64` 保留原文） |

**反例**：`C:\x64`、`C:\x64\bin`、`C:\x64\`、`\x6` 不探测。

**限制与典型误识别**：

- 形如 `\x86\x64` 的连续目录名会触发解码：`C:\build\x86\x64\` 解码出非法 UTF-8，
  产生 `DECODE_FAILED` 警告（失败只影响该候选）。
- 不支持 `\x{...}`、八进制 `\NNN`、`0xNN` 等其他写法。

### D-ESC-U-001

**条件**：与 D-ESC-X-001 相同的探测与原位替换规则，单元为 `\uHHHH`，
末尾不完整单元为 `\u` 加 0–3 个十六进制数字。单元按 UTF-16 代码单元组合，代理项对合并为一个字符
（`charset: "utf-16"`，`byte_count` 为解码文本的 UTF-8 长度）。

**失败报告**：孤立代理项报告 `lone_surrogate`；非文本报告 `not_text`；只有被截断单元时静默。
位于已观察载荷末尾的高代理项视为低代理项被截断：不计入文本，视图标记为 partial。

| 输入 | 结果 |
|---|---|
| `\u0063\u006d\u0064` | `cmd` |
| `\ud83d\ude00\u0041` | `😀A` |
| `\u0041\u00` | `A`，partial |
| `\u0041\ud83d` | `A`，partial |
| `\ude00\u0041` | 孤立低代理项，报告 `DECODE_FAILED` |

**反例**：`C:\u0041bc`（单个单元）、`\users\name`（不是十六进制）、`C:\u0041\` 不探测；
`\ud83d\ude0` 只有被截断的代理项对，静默丢弃。

**限制**：不支持 `\U0010FFFF`、`\u{...}`、`&#x...;` 等其他写法。

---

## 资源上限与受限标记

上限定义见 OUTPUT_FORMAT.md 第 9 节。任一上限被触发时：

- 返回已完成的部分结果；`processing.limited` 为 `true`，`processing.limits` 列出每个上限的名称、
  上限值与触发次数；
- `notices` 末尾为每个上限追加一条 `LIMIT_REACHED`（level `limit`），指向首次触发位置；
- 受限不是语法错误，CLI 退出码仍为 0。

## 已知限制汇总

- 不识别任何 Shell 语法：引号、caret、反斜杠转义、变量、子命令都不求值，符号不带语义。
- 引号分组不模拟反斜杠转义；自然语言撇号会开启分组。
- 不做 NFKC、同形字符等 Unicode 归一化；UTF-8 BOM 作为原文保留在第一个 Token 中。
- Base64 形态的普通单词会被尝试并静默失败，占用解码尝试预算；带提示时门槛很低。
- 只有 `-enc`、`-encodedcommand` 产生提示，且只作用于下一个 Token。
- 无 padding 且长度不是 4 的倍数的 Base64 按截断处理（partial）。
- 百分号候选末尾的 `%VAR%` 可能导致 partial；单个 `%HH` 的失败不报告。
- 只剩被截断单元、没有任何文本的载荷静默丢弃。
- `\x`/`\u` 转义：连续单元后的孤立反斜杠视为截断单元；单个单元不触发。
- 解码只尝试 UTF-8 与 UTF-16LE。
- 首版阈值只基于合成样例，未在真实日志上评估误报率与召回率。
