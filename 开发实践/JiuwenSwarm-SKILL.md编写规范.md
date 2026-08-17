# JiuwenSwarm SKILL.md 编写规范(v1.0)

> 适用范围:JiuwenSwarm `agent/skills/<skill-name>/SKILL.md` 与 `jiuwenswarm/agents/swarm/...` 下技能定义
> 规范依据:`jiuwenswarm/server/runtime/skill/skill_manager.py::_parse_skill_md` 实际解析逻辑
> 适用版本:jiuwenswarm ≥ 0.2.4.beta4

---

## 一、SKILL.md 是什么

JiuwenSwarm 中的 **Skill** 是一个可被多 Agent 复用、可被 Skill 自演进机制优化、可在 Swarm Skills Hub 中共享的能力单元。每个 Skill 由以下三部分构成:

```
skills/<skill-name>/
├── SKILL.md          ← 必选,YAML frontmatter + Markdown 正文
├── scripts/          ← 可选,被正文引用的脚本
├── references/       ← 可选,正文中链接的参考资料
├── assets/           ← 可选,正文中链接的图片/模板
└── templates/        ← 可选,正文中使用的输出模板
```

框架通过 `SkillManager._parse_skill_md()` 解析 SKILL.md,提取 frontmatter(元数据)与 body(正文),将正文作为 Skill 的"操作手册"注入到 Agent 的上下文中。Agent 根据正文决定**何时**(trigger)、**如何**(workflow)、**产出什么**(output)调用本 Skill。

---

## 二、SKILL.md 结构总览

```markdown
---
<YAML frontmatter: 元数据,必须>
---

<Markdown 正文: 三段式 = 概述 + 工作流 + 约束>

## 使用脚本
## 解释器选择
## 执行
## 产物
## 场景
## 输出结论
```

参考样例:`tests/ui_e2e/SKILL.md`(唯一一个已存在的 SKILL.md)。

---

## 三、YAML Frontmatter 规范

### 3.1 字段清单

| 字段 | 必选 | 类型 | 用途 | 备注 |
|---|---|---|---|---|
| `name` | ✅ | str | Skill 唯一标识,必须与目录名一致 | 框架会从文件名兜底推断,但**强烈建议显式声明** |
| `description` | ✅ | str | Skill 触发条件,被 Agent 用于匹配 | **这是最关键的字段** — 直接决定 Skill 何时被调用 |
| `allowed_tools` | ⭐ | str/list | 本 Skill 允许调用的工具列表 | 工具权限闸门,与 5天计划"工具权限与安全防护"对应 |
| `version` | ⭐ | str | 语义化版本,自演进用 | 缺省 `""` |
| `author` | ⭐ | str | 作者/团队 | 缺省 `""` |
| `tags` | ⭐ | str/list | 检索标签,被 RAG 索引 | 用于 Swarm Skills Hub 搜索 |

### 3.2 description 字段写法(核心)

`description` 是 Agent 决定是否调用本 Skill 的唯一依据。规范要求:

**1. 长度**:1-3 句话,40-200 字符。Agent 在上下文中只能看到前 256 token,过长的 description 会被截断。

**2. 句式**:**动词 + 对象 + 触发场景**。三段式:
- 做什么(动作)
- 在什么场景下(触发条件)
- 不做什么(边界,可选)

**3. 触发关键词必须出现**。Agent 用关键词模糊匹配,关键场景词必须字面出现在 description 中。

**4. 反例对照**:

```yaml
# ❌ 反例 1:过于抽象,无触发关键词
description: 金融分析技能

# ❌ 反例 2:实现细节,不是触发条件
description: 使用 akshare 获取 stock_zh_a_hist 数据,adjust='qfq' 前复权,return DataFrame

# ✅ 正例:动词+对象+场景
description: |
  加载上市公司池(板块→代码映射)、校验投资标的是否在白名单、
  采集 A 股个股的日线行情与三大表财报。在选股决策、单标的研报、
  投资组合构建或需要"投资标的限定"校验时使用。
```

### 3.3 allowed_tools 字段规范

框架会拒绝 Skill 调用未声明的工具。规范:

```yaml
# 列表形式(推荐)
allowed_tools:
  - mcp.financial.akshare_quote
  - mcp.financial.akshare_filing
  - mcp.excel.read_xlsx
  - file.write_report

# 逗号分隔字符串(框架兼容,但不推荐)
allowed_tools: "mcp.financial.akshare_quote, mcp.financial.akshare_filing"
```

**反例**:

```yaml
# ❌ 写成 "all" 等同于关闭权限闸门,违反安全规范
allowed_tools: all

# ❌ 缺省会让框架默认空权限,Skill 跑不通
# (完全省略 allowed_tools 字段)
```

### 3.4 name 字段强约束

- 必须与目录名一致:目录 `skills/finance-report/` → `name: finance-report`
- 必须 `kebab-case`(小写 + 连字符)
- 不允许下划线、空格、大写

---

## 四、正文(Markdown Body)规范

### 4.1 推荐章节结构

```markdown
# <Skill 标题>

<一段话简介:在 50 字内说清本 Skill 的目标>

## 使用脚本  (或:使用工具)
- 列具体可执行入口

## 输入 / 输出
- 输入:参数与约束
- 输出:产物落盘位置

## 执行
```bash
<最常用的 1-3 条命令>
```

## 场景
- 列举典型使用情境,与 description 中的触发关键词一一对应

## 输出结论
- 调用本 Skill 后,产出物的位置、格式、命名规范

## 异常处理 (可选)
- 失败时如何降级,降级产物在哪里

## 与其他 Skill 的边界 (可选)
- 与 finance-deep-research、finance-report-writer 等的职责划分
```

### 4.2 正文写作原则

**1. "可执行"高于"可读"**
- 命令必须能直接复制运行,不要写"类似 `xxx`"
- 路径用相对路径,从 Skill 目录起算

**2. "约束"必须显式**
- 凡是 Agent 容易踩坑的地方,必须用 `>` 引用块或 **加粗** 标出
- 例:>`**白名单越界处理**:传入非池内代码必须抛 WhitelistViolation,不要静默忽略`

**3. 不要重复 frontmatter 已表达的信息**
- description 已经说"做什么",正文应专注"怎么做"

**4. 示例优先于抽象描述**
- 写 `python scripts/quote_collector.py 600519 --days 365`,不写"调用 quote_collector 采集一年数据"

### 4.3 代码块与命令规范

```markdown
## 执行

采集单标的近一年行情:

```bash
python scripts/quote_collector.py 600519 --days 365 --output data/600519_quote.json
```

按板块批量采集(并发=3):

```bash
python scripts/quote_collector.py --sector 白酒 --concurrency 3
```
```

- 必须用代码块(三个反引号 + bash)
- 命令要带常用 flag,降低 Agent 试错成本

---

## 五、典型反模式与对策

| 反模式 | 后果 | 对策 |
|---|---|---|
| description 写成"金融分析"等抽象词 | Agent 无法触发 | 列出 3-5 个具体场景关键词 |
| 正文直接大段贴代码 | 评审与维护困难 | 代码放 `scripts/`,正文只引用 |
| 工具权限 `all` | 安全审计失败 | 显式列出 `allowed_tools` |
| 缺 `version` 字段 | 自演进机制无法追踪 | 加 `version: 0.1.0` |
| 没有"异常处理"章节 | Agent 失败后无降级路径 | 显式说明失败产物位置 |
| description 与 name 不一致 | 框架元数据错位 | name = 目录名,description 首句点出 name |

---

## 六、模板:可直接复用的 SKILL.md

```markdown
---
name: <skill-name>
description: |
  <一句话动作 + 触发场景 + 边界>。
  在 <关键词 1>、<关键词 2>、<关键词 3> 或 <典型情境> 时使用。
version: 0.1.0
author: <团队或个人>
tags:
  - <标签 1>
  - <标签 2>
allowed_tools:
  - <tool 1>
  - <tool 2>
---

# <Skill 中文名>(<skill-name>)

<50 字内简介:目标 + 与上下游 Skill 的关系>

## 使用脚本

- `scripts/<入口 1>.py` — <职责>
- `scripts/<入口 2>.py` — <职责>
- `references/<文档>.md` — <可选参考资料>

## 输入与输出

- 输入:<参数清单 + 格式(JSON/YAML/CLI flag)>
- 输出:<产物路径 + 文件格式 + 命名规范>

## 执行

<最常用的 1-3 条命令,带典型 flag>

```bash
python scripts/<entry>.py <典型参数>
```

## 场景

- <场景 1:与 description 关键词呼应>
- <场景 2>
- <场景 3>

## 输出结论

- 实际执行的命令
- 使用的运行时解释器
- 报告/数据目录
- 每个步骤的通过或失败状态
- 第一处可操作的失败信息
- 对应证据文件名

## 异常处理

- **数据源失败**:<降级策略>
- **白名单越界**:<拒绝策略>
- **超时**:<重试与跳过策略>

## 与其他 Skill 的边界

- 上游依赖:<谁会调用我>
- 下游消费:<我的产物被谁使用>
- 禁止做:<明确边界,避免职责蔓延>
```

---

## 七、样例:为 Day 1 缺失的 `finance-report` Skill 写一个参考 SKILL.md

```markdown
---
name: finance-report
description: |
  加载上市公司池(板块→代码映射)、校验投资标的是否在白名单、
  采集 A 股个股的日线行情与三大表财报。
  在选股决策、单标的研报、投资组合构建或需要"投资标的限定"校验时使用。
  不做财务分析、报告生成、组合权重计算(由 finance-analyzer / report-writer / investor 负责)。
version: 0.1.0
author: jiuwenswarm-finance-team
tags:
  - finance
  - data-collection
  - whitelist
allowed_tools:
  - mcp.financial.akshare_quote
  - mcp.financial.akshare_filing
  - mcp.excel.read_xlsx
  - file.write_json
---

# 金融分析报告生成 — 数据采集层(finance-report)

为下游分析与报告生成提供"原始数据"与"白名单闸门"。
所有数据落盘到 `reports/finance-report/data/<symbol>_<kind>.json`,可被 RAG 与 CodeExecutor 直接消费。

## 使用脚本

- `collectors/pool_loader.py` — 解析 `example/上市公司列表.xlsx`,提供板块分组与白名单校验
- `collectors/quote_collector.py` — 行情采集(腾讯 ifzq.gtimg.cn 前复权接口)
- `collectors/filing_collector.py` — 财报采集(akshare `stock_financial_abstract` + 衍生指标计算)

## 输入与输出

- 输入:股票代码(如 `600519`)或板块名(如 `白酒`)
- 输出:
  - 行情:`reports/finance-report/data/<symbol>_quote.json`
  - 财报:`reports/finance-report/data/<symbol>_filing.json`
  - 公司池缓存:`reports/finance-report/.cache/pool.json`

## 执行

采集单标的近一年行情:

```bash
python -m finance_report.collectors.quote_collector 600519 --days 365
```

按板块批量采集(并发=3,失败重试 1 次):

```bash
python -m finance_report.collectors.quote_collector --sector 白酒 --concurrency 3
```

加载公司池并校验白名单:

```bash
python -m finance_report.collectors.pool_loader --validate 600519
```

## 场景

- 单标的研报准备:采集 600519 行情 + 财报
- 全公司池批量:按板块分批并发采集
- 白名单校验:Investor / Planner 传入代码前调用 `assert_in_whitelist`

## 输出结论

- 实际执行的命令
- 使用的运行时解释器(必须 ≥ Python 3.11)
- 数据文件落盘路径
- 每只标的的采集状态(success / failed / skipped)
- 第一处失败的代码 + 错误信息
- 失败标的列表(供 ReportWriter 标注"数据缺失")

## 异常处理

> **白名单越界**:传入非池内代码必须抛 `WhitelistViolation`,**不要静默忽略**。
> audit_log 写入 `reports/finance-report/.audit/whitelist_violations.jsonl`。

- **数据源超时**:单标的 3 次重试后跳过,在结果中标 `failed`
- **板块全部失败**:保留已有缓存,输出 warning,继续走下游
- **akshare 接口变更**:`FilingCollector` 提供 `--fallback-sina` flag,降级到新浪接口

## 与其他 Skill 的边界

- 上游:PlannerAgent(选股决策)、ReviewerAgent(溯源校验)
- 下游:finance-analyzer(读取 JSON 做分析)、finance-report-writer(读取 JSON 写报告)
- **禁止做**:财务指标计算、报告撰写、组合权重
```

---

## 八、SKILL.md 自检清单

写完后,逐项过一遍:

- [ ] `name` 字段与目录名一致
- [ ] `description` 包含 3-5 个具体触发关键词
- [ ] `allowed_tools` 显式列出,**不是** `all`
- [ ] `version` 字段不为空
- [ ] 正文有"执行"代码块,且能复制运行
- [ ] 显式说明"异常处理"或降级策略
- [ ] 显式说明"边界"或上下游 Skill
- [ ] 写完后跑一次 `SkillManager._parse_skill_md(path)` 自检(单元测试覆盖)

---

## 九、与现有样例的对照

`tests/ui_e2e/SKILL.md` 是一个**正面样例**:frontmatter 完整、description 含触发关键词("运行 ... 端到端测试"、"验证 Todo 和 Cron Web UI 流程"、"复现浏览器交互问题")、正文有"使用脚本 / 准备环境 / 执行 / 产物 / 场景 / 输出结论"标准六段。

**对比建议**:Day 1 的 `finance-report/SKILL.md` 模板完全沿用该六段式,可保证通过 `SkillManager._parse_skill_md` 解析 + Swarm Skills Hub 检索。

---

## 十、版本与维护

- 规范版本:v1.0(2026-08-16)
- 维护人:Claude Code + jiuwenswarm-finance-team
- 反馈方式:在本文件同目录新增 `SKILL.md-FAQ.md` 记录常见问题
- 后续版本会补充:MCP 工具声明规范、Skill 自演进触发条件、跨 Skill 引用语法

---

> 参考实现:`jiuwenswarm/server/runtime/skill/skill_manager.py:2690-2752`
> 参考样例:`tests/ui_e2e/SKILL.md`
