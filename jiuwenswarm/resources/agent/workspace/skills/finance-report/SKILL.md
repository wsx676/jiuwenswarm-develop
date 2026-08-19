---
name: finance-report
version: 2.1.0
kind: swarm-skill
description: 金融分析与投资决策 Agent：在组委会指定上市公司池（A股六大板块）内自主选股并输出仓位配置（Portfolio.json），批量生成个股投资研报（股票代码.md）。深度耦合 JiuwenSwarm 框架（Leader-Team 多智能体 + Swarmflow + RAG + MCP + CodeExecutor）。Use when user asks for company/industry/macro financial reports or investment portfolio decisions.
tags: [finance, report, agent, rag, mcp, portfolio, investment]
allowed_tools: [bash, read_file, write_file, read_memory, write_memory, mcp]
---

# 金融分析与投资决策 Agent

在组委会指定的上市公司池（六大板块，见 `example/上市公司列表.xlsx`）内自主选股，输出投资组合配置（Portfolio.json）与个股投资研报（股票代码.md），支持批量产出与空仓决策。

## 支持的任务类型

| 类型 | 触发关键词 | 示例 |
|------|-----------|------|
| 公司研报 | "公司研报""个股分析""XX公司" | 生成贵州茅台公司研报 |
| 行业研报 | "行业研报""行业分析" | 生成消费板块行业研报 |
| 宏观研报 | "宏观研报""策略报告" | 生成宏观经济季度研报 |
| 投资决策 | "投资决策""仓位配置""组合配置" | 基于分析结果输出投资组合 |

> 三类研报均已端到端落地：公司研报（八章）、行业研报（八章，板块景气度+竞对横向对比+净利润对比图）、宏观研报（七章，GDP/CPI/PMI+政策+板块配置建议）；撰写为 LLM 优先、规则模板兜底，统一过引用率/结构/合规审查闸门。

## 目录结构

```plain
finance-report/
├── SKILL.md                    # 技能定义（本文件）
├── run_report.py               # 命令行入口脚本
├── orchestrator.py             # 多 Agent 编排入口（含自检反馈循环）
├── scripts/
│   └── workflow.py             # Swarmflow 确定性五阶段工作流（Day 5）
├── agents/                     # 子 Agent 定义
│   ├── planner.py              # 任务规划 Agent
│   ├── researcher.py           # 数据研究 Agent（迭代式 Deep Research）
│   ├── writer.py               # 报告撰写 Agent（分治式生成）
│   ├── reviewer.py             # 审查校验 Agent（溯源/图文/结构/合规）
│   └── investor.py             # 投资决策 Agent（选股评分+仓位配置）
├── collectors/                 # 数据采集层
│   ├── pool_loader.py          # 公司池加载与白名单校验
│   ├── quote_collector.py      # 行情数据采集（支持板块批量）
│   ├── news_collector.py       # 新闻政策采集（迭代式 Deep Research）
│   ├── filing_collector.py     # 公司披露采集（三大表）
│   └── rag_retriever.py        # 财务知识 RAG 检索
├── common/                     # 公共能力
│   ├── llm_client.py           # LLM 客户端（Anthropic 协议，含 token 用量统计）
│   ├── telemetry.py            # 运行遥测：阶段耗时/Token 消耗/随机种子（Day 5）
│   └── hybrid_memory.py        # 混合记忆分流
├── analyzers/                  # 分析引擎层
│   ├── code_executor.py        # CodeExecutor（Notebook式执行+AST白名单）
│   ├── finance_analyzer.py     # 财务分析（盈利/偿债/成长/估值）
│   ├── industry_analyzer.py    # 行业分析（板块竞对两两对比）
│   └── macro_analyzer.py       # 宏观分析（GDP/CPI/PMI/政策）
├── generators/                 # 报告生成层
│   ├── chart_generator.py      # 图表生成（图文同源）
│   ├── report_writer.py        # 结构化报告撰写（先大纲后分段）
│   └── citation_checker.py     # 事实溯源校验
└── templates/                  # 报告模板
    └── company_report.md       # 公司研报模板
```

## 使用方式

本技能通过技能内封装的 Python 模块采集数据与分析（经框架执行，不使用独立脚本绕过框架）。

### 依赖安装

```bash
pip install pandas numpy matplotlib akshare openpyxl ipython
pip install chromadb sentence-transformers   # RAG（可选，外部记忆）
```

### 生成公司研报（公司池内指定标的，输出 600519.md）

```bash
python run_report.py company --target 600519 --name 贵州茅台 --save
```

### 生成行业研报

```bash
python run_report.py industry --name 消费板块 --save
```

### 生成宏观研报

```bash
python run_report.py macro --period 2026Q2 --save
```

### 投资决策（公司池选股 + 仓位配置，输出 Portfolio.json）

```bash
python run_report.py invest --pool-file example/上市公司列表.xlsx --save
```

### 公司池校验 / 采集 / 分析两阶段（Day 5，供 Swarmflow 工作流调用）

```bash
python run_report.py pool                                  # 枚举板块与标的清单
python run_report.py research --stage collect              # 全池采集（缓存优先，可按 --sector 单板块）
python run_report.py research --stage analyze --save       # 读缓存分析 + 因子打分，评分缓存落盘
python run_report.py invest --pool-file example/上市公司列表.xlsx --use-cached-scores --skip-reports --save
```

## Workflow

全流程封装为 Swarmflow 确定性五阶段工作流（`scripts/workflow.py`，
可由 TUI SwarmFlow 直接调度），阶段间状态落盘传递、失败自动重试一次：

1. **选股**：校验公司池白名单，枚举六大板块与标的清单（`pool` 子命令）
2. **采集**：按板块扇出逐标的采集行情/财报/新闻，缓存落盘 `data/`（断点续采）
3. **分析**：读已采集数据跑分析引擎并因子打分，评分缓存 `decision_log/scores_cache.json`
4. **决策**：复用评分缓存做风控约束仓位配置，产出 `Portfolio.json` + 决策日志
5. **报告**：仅为入选标的逐个生成个股研报（`{股票代码}.md`）

入参：`{"pool_file": "example/上市公司列表.xlsx", "sector": ""}`（sector 非空时只跑该板块）。

## 执行流程

1. **Planner** 判断任务类型，拆解采集与分析子任务
2. **Researcher** 迭代式 Deep Research 采集行情/财报/新闻，RAG 检索财务知识（混合记忆分流，大表格"头尾各5行"压缩）
3. **Analyzers** 通过 CodeExecutor 执行财务/行业/宏观分析代码（板块内竞对两两对比）
4. **Writer** 分治式生成报告：先 YAML 大纲 → 逐段撰写 → 图片本地化
5. **Reviewer** 校验溯源/图文一致/结构/合规，不通过回流重写（≤2 轮）
6. **Investor** 基于研报结论评分选股，输出 `Portfolio.json`（支持满仓/半仓/空仓）

## ⚠️ 重要约束

1. **投资标的限定**：仅可在组委会公布的上市公司列表内选择；判定均无投资价值时可空仓，但须在报告中阐明决策逻辑
2. **事实溯源**：所有数据与论据必须标注来源，CitationChecker 会逐条校验（引用率 ≥ 90%）
3. **禁止编造**：模型不得生成无法溯源的数据，无数据时应明确标注"暂无公开数据"
4. **图文一致**：图表与正文必须使用同一份数据，禁止图表与文字数据矛盾
5. **成果可复现**：决策过程与资源消耗须记录在案，第三方可凭代码与环境说明复现
6. **框架约束**：不得以未经封装的独立脚本绕过 openJiuwen 框架约束

## 输出规范

- 个股研报：`reports/finance-report/个股投资研报/{股票代码}.md`
- 投资组合：`reports/finance-report/Portfolio.json`（`{"股票代码": 持仓占比}`，单标的权重 ≤ 0.4，权重之和 ≤ 1.0）
- 图表：`reports/finance-report/charts/`（本地图片，报告仅引用真实存在的本地图片）
- 决策日志：`reports/finance-report/decision_log/`（决策过程与资源消耗记录）

## Files

| 文件 | 说明 |
| --- | --- |
| `scripts/workflow.py` | Swarmflow 五阶段确定性工作流（本技能执行定义） |
| `run_report.py` | CLI 入口（pool/research/company/industry/macro/invest 六子命令） |
| `orchestrator.py` | 多 Agent 编排（含阶段计时与批量容错） |
| `common/telemetry.py` | 遥测：随机种子/阶段耗时/LLM token 消耗 → `decision_log/run_stats.json` |
| `reports/finance-report/decision_log/decision.json` | 决策日志（评分/权重/空仓理由/失败留痕） |
| `reports/finance-report/decision_log/scores_cache.json` | 分析阶段评分缓存（阶段间状态传递） |
| `reports/finance-report/README.md` | 环境说明与复现步骤（依赖清单 + 配置） |
