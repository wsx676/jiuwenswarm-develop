---
name: finance-report
version: 2.0.0
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
| 行业研报 | "行业研报""行业分析" | 生成半导体行业研报 |
| 宏观研报 | "宏观研报""策略报告" | 生成宏观经济季度研报 |
| 投资决策 | "投资决策""仓位配置""组合配置" | 基于分析结果输出投资组合 |

## 目录结构

```plain
finance-report/
├── SKILL.md                    # 技能定义（本文件）
├── run_report.py               # 命令行入口脚本
├── orchestrator.py             # 多 Agent 编排入口（含自检反馈循环）
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
python run_report.py industry --name 半导体 --save
```

### 生成宏观研报

```bash
python run_report.py macro --period 2026Q2 --save
```

### 投资决策（公司池选股 + 仓位配置，输出 Portfolio.json）

```bash
python run_report.py invest --pool-file example/上市公司列表.xlsx --save
```

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
