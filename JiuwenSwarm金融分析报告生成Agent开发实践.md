# 基于 JiuwenSwarm 的 Agent 金融分析报告生成｜开发实践

## 引言｜让 Agent 真正做出一份可用的投资决策

投资分析报告是投资决策的核心产出。一份合格的金融分析报告要同时满足：**专业深度**（分析过程充分、方法论合理）、**数据可溯源**（每个数据有明确来源）、**决策可支撑**（结论能直接支撑选股与仓位配置）、**成果可复现**（第三方可依据代码与环境说明复现决策过程）。

直接把大模型拿来做金融分析，会撞上四堵墙：

1. **财务分析能力缺失**——通用模型缺乏对财报、估值、行业逻辑的专业理解；
2. **信息获取与整合不足**——模型训练语料滞后，无法拿到实时行情、最新公告与政策；
3. **幻觉与结构化输出困难**——模型会编造数据，且难以稳定产出带图表、带引用的规范结构；
4. **投资决策能力缺失**——会写分析不等于会做投资，缺少从分析结论到选股与仓位配置的决策闭环。

> 赛题来源：【华为openJiuwen】基于 JiuwenSwarm 的 Agent 金融分析报告生成（比赛要求详见工作区根目录 `要求.md`）

**赛题任务**：基于 openJiuwen（JiuwenSwarm）框架开发能自主完成金融分析的智能体，在组委会指定的上市公司范围内**选股决策**并给出**仓位配置方案**（可满仓/半仓/空仓），同时产出相应投资分析报告。作品验证期开始前提交最终投资组合结果（上市公司代码 + 持仓占比）。

**赛题关键要求**：

| 要求 | 内容要点 |
| --- | --- |
| **框架强制性** | 必须深度耦合 openJiuwen 核心能力模块，不得使用未经封装的独立脚本绕过框架约束 |
| **投资标的限定** | 严格限定于组委会公布的上市公司列表（A 股沪深、六大板块，见 `example/上市公司列表.xlsx`）；允许空仓，但须在报告中阐明决策逻辑 |
| **报告分析维度** | 技术分析、基本面分析、宏观经济分析、另类数据挖掘、情绪因子构建等 |
| **评测规则** | 初赛仅依据收益率及风险控制指标客观排名；决赛考察投资报告完整性、逻辑严谨性与答辩表现 |
| **成果可复现性** | 第三方可依据代码与环境说明完整复现投资决策过程及资源消耗数据 |
| **提交格式** | `Portfolio.json`（`{"股票代码": 持仓占比}`）+ `个股投资研报/股票代码.md`（见 `example/赛题二提交样例`） |

本实践基于 **JiuwenSwarm** 开源 Agent 框架，构建一套能在指定公司池内自主完成**选股决策 + 仓位配置 + 投资报告**的多智能体系统。技术栈覆盖：Leader-Team 多智能体协同、Swarmflow 确定性工作流、技能系统（SKILL.md + Python 模块）、CodeExecutor 代码执行器、RAG 检索增强、MCP 工具协议、混合记忆系统与事实溯源。

---

## 项目环境说明

> 本文档基于真实开发项目编写，配置与代码均为实际可用版本。

### 运行环境

| 项目 | 配置值 |
| --- | --- |
| **项目路径** | `D:\Download\jiuwenswarm` |
| **操作系统** | Windows 10 / Linux |
| **Python** | 3.10+ |
| **模型服务** | 开源模型（Qwen3-235B / DeepSeek-V3 等），OpenAI 兼容接口 |

### 核心文件位置

```plain
D:\Download\jiuwenswarm\
├── .env                              # 环境变量配置
├── config/config.yaml                # 应用配置（模型、心跳、频道）
├── workspace/
│   ├── HEARTBEAT.md                  # 心跳任务配置
│   └── agent/
│       ├── reports/finance-report/   # 研报输出目录
│       ├── memory/                   # 财务知识库 / 向量索引
│       └── skills/finance-report/    # 技能模块
│           ├── SKILL.md              # 技能定义
│           ├── orchestrator.py       # 多 Agent 编排入口
│           ├── collectors/           # 数据采集层
│           ├── analyzers/            # 分析引擎层
│           ├── generators/           # 报告生成层
│           └── agents/               # 子 Agent 定义
```

---

## 一、问题背景

### 1.1 四大核心挑战

| 挑战 | 表现 | 本方案对策 |
| --- | --- | --- |
| **财务分析能力缺失** | 模型不懂估值模型、财务勾稽 | 财务分析引擎 + CodeExecutor 代码执行器 + 财务知识 RAG，将专业分析方法论沉淀为可调用模块 |
| **信息获取与整合不足** | 语料滞后、拿不到实时数据 | 数据采集层实时抓取行情/公告/新闻 + 迭代式 Deep Research 至信息饱和 |
| **幻觉与结构化输出困难** | 编造数据、结构混乱 | 多 Agent 自检反馈循环 + 事实溯源 + 结构化模板约束 |
| **投资决策能力缺失** | 会写分析不会做投资 | Investor Agent 完成投资评分与仓位配置，输出 `Portfolio.json`，支持满仓/半仓/空仓 |

### 1.2 报告的三大类型

| 类型 | 关注点 | 典型数据 |
| --- | --- | --- |
| **宏观/策略报告** | 宏观经济指标、政策趋势、大类资产配置 | GDP/CPI/PMI、利率汇率、政策文件 |
| **行业/子行业报告** | 行业景气度、竞争格局、产业链 | 行业指数、上下游量价、产能数据 |
| **公司/个股报告** | 财务质量、估值、盈利预测、投资结论 | 财报三表、估值指标、公司公告 |

三类报告的数据源、分析逻辑、章节结构差异很大，要求 Agent 系统具备**任务泛化能力**——同一套框架能稳定输出不同公司、不同行业的报告。其中**公司/个股报告是比赛核心产出**（按 `股票代码.md` 命名批量交付），直接支撑选股与仓位决策。

### 1.3 JiuwenSwarm 框架适配性

赛题要求 Agent 实现**深度耦合 openJiuwen 核心能力模块，不得使用未经封装的独立脚本绕过框架约束**。JiuwenSwarm 的能力模块与本方案逐一对应：

| 能力 | 框架支持 | 在本方案的作用 |
| --- | --- | --- |
| **技能系统 (Skill)** | `SKILL.md` + Python 模块 | 封装研报生成与投资决策全流程 |
| **Swarmflow 确定性工作流** | 多阶段工作流定义与调度 | 选股→分析→决策→报告各阶段编排，支持状态传递、错误重试与 Token 预算控制 |
| **Leader-Team 多智能体协同** | Leader 拆解任务、动态组建专业团队 | Planner/Researcher/Writer/Reviewer/Investor 团队协同 |
| **Symphony 编排** | 技能编排与分发 | 多 Agent 任务调度与状态同步 |
| **MCP 协议** | MCP 配置接入外部工具 | 行情查询、公告检索、新闻搜索工具 |
| **记忆系统** | 短期/长期记忆 | 混合记忆管理、财务知识库、研报模板沉淀 |
| **TUI 运行树监控** | 实时查看阶段状态、耗时与资源消耗 | 满足成果可复现性中的资源消耗记录要求 |
| **CodeExecutor 代码执行器** | Notebook 式持久化代码执行 + AST 白名单 | 财务计算、图表生成等动态代码安全执行 |

---

## 二、技术方案

### 2.1 整体分层架构

研报生成 Agent 在 JiuwenSwarm 的 Application Layer（应用层），其下复用框架的编排层、执行层与基础层：

```
┌─────────────────────────────────────────────────────────────┐
│                   Application Layer                          │
│              (finance-report skill)                          │
│                                                              │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐ │
│   │Orchestr- │→  │Collectors│→  │Analyzers │→  │Generators│ │
│   │  ator    │   │ 数据采集  │   │ 分析引擎  │   │ 报告生成 │ │
│   │多Agent编排│   │          │   │          │   │         │ │
│   └────┬─────┘   └──────────┘   └──────────┘   └─────────┘ │
│        │  调度子Agent: Planner / Researcher / Writer / Reviewer │
├────────┼──────────────────────────────────────────────────────┤
│        │  Orchestration Layer (编排层)                          │
│        └→ Symphony 编排 · A2A 协作 · Heartbeat                  │
├──────────────────────────────────────────────────────────────┤
│  Execution Layer: FileTools · MemoryTools · MCP Tools         │
│  Foundation Layer: RAG 向量检索 · Prompts · LLM(开源模型)      │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 数据处理流程

完整的研报生成流程：

```
用户请求/心跳触发: 生成 XX 公司研报
         │
         ▼
┌──────────────────┐
│   ORCHESTRATOR   │  任务拆解
│  Planner Agent   │  判断研报类型 → 拆分子任务 → 分配子Agent
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   COLLECTORS     │  多源数据采集
│  QuoteCollector  │  行情数据 (收盘价/成交量/指数)
│  NewsCollector   │  新闻政策 (财经媒体/政府文件)
│  FilingCollector │  公司披露 (财报/公告)
│  RAGRetriever    │  财务知识库 向量检索
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   ANALYZERS      │  专业分析
│  FinanceAnalyzer │  财务三表分析/估值/盈利预测
│  IndustryAnalyzer│  行业景气度/竞争格局/产业链
│  MacroAnalyzer   │  宏观指标/政策趋势
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   GENERATORS     │  多模态报告
│  ChartGenerator  │  走势图/对比图/财务表格
│  ReportWriter    │  结构化Markdown (论点-论据-引用)
│  CitationChecker │  事实溯源校验
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   REVIEWER       │  自检反馈循环
│  事实校验/逻辑审查 │  不合格 → 回流重写
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│    OUTPUT        │  结果输出
│  本地研报 .md     │  含图表 + 引用标注 + 风险提示
│  飞书推送         │  交互式卡片
└──────────────────┘
```

### 2.3 核心组件概览

| 组件 | 类型 | 职责 | 所在模块 |
| --- | --- | --- | --- |
| **Orchestrator** | 编排器 | 多 Agent 任务拆解与调度 | `orchestrator.py` |
| **QuoteCollector** | Collector | 股票/指数历史行情采集 | `collectors/quote_collector.py` |
| **NewsCollector** | Collector | 财经新闻与政策文件采集 | `collectors/news_collector.py` |
| **FilingCollector** | Collector | 上市公司财报与公告采集 | `collectors/filing_collector.py` |
| **RAGRetriever** | Collector | 财务知识库向量检索 | `collectors/rag_retriever.py` |
| **FinanceAnalyzer** | Analyzer | 财务三表分析与估值 | `analyzers/finance_analyzer.py` |
| **IndustryAnalyzer** | Analyzer | 行业景气度与竞争格局 | `analyzers/industry_analyzer.py` |
| **MacroAnalyzer** | Analyzer | 宏观指标与政策趋势 | `analyzers/macro_analyzer.py` |
| **ChartGenerator** | Generator | 多模态图表生成 | `generators/chart_generator.py` |
| **ReportWriter** | Generator | 结构化研报撰写 | `generators/report_writer.py` |
| **CitationChecker** | Generator | 事实溯源与引用校验 | `generators/citation_checker.py` |
| **InvestorAgent** | Agent | 选股评分与仓位配置（输出 Portfolio.json） | `agents/investor.py` |
| **CodeExecutor** | 执行器 | Notebook 式代码执行（AST 白名单） | `analyzers/code_executor.py` |

### 2.4 评测维度与方案对应

比赛分初赛与决赛两个阶段，评测重点不同，本方案分别应对：

| 阶段 | 评测依据 | 方案对应 |
| --- | --- | --- |
| **初赛（客观）** | 仅依据收益率及风险控制指标排名 | Investor Agent 选股评分 + 仓位配置 + 风险控制约束（仓位上限/分散度） |
| **决赛（综合）** | 投资报告完整性、逻辑严谨性、答辩表现 | 结构化模板 + Reviewer 自检反馈循环 + CitationChecker 事实溯源 |

同时须满足过程性要求：

| 要求 | 方案对应 |
| --- | --- |
| 框架强制性 | 全部能力封装为 JiuwenSwarm 技能/团队/Swarmflow，无独立脚本绕过 |
| 成果可复现性 | 决策过程日志 + 资源消耗记录（Swarmflow 运行树）+ 代码与环境说明 |
| 提交格式 | `Portfolio.json` + `个股投资研报/股票代码.md` 批量产出 |

---

## 第三章｜Skills 技能系统工程化设计

### 3.1 Skills 目录结构

```plain
workspace/agent/skills/finance-report/
├── SKILL.md                    # 技能定义（必须）
├── orchestrator.py             # 多 Agent 编排入口
├── run_report.py               # 命令行入口脚本
│
├── agents/                     # 子 Agent 定义
│   ├── __init__.py
│   ├── planner.py              # 任务规划 Agent
│   ├── researcher.py           # 数据研究 Agent
│   ├── writer.py               # 报告撰写 Agent
│   └── reviewer.py             # 审查校验 Agent
│
├── collectors/                 # 数据采集层
│   ├── __init__.py
│   ├── quote_collector.py      # 行情数据采集
│   ├── news_collector.py       # 新闻政策采集
│   ├── filing_collector.py     # 公司披露采集
│   └── rag_retriever.py        # 财务知识 RAG 检索
│
├── analyzers/                  # 分析引擎层
│   ├── __init__.py
│   ├── finance_analyzer.py     # 财务分析
│   ├── industry_analyzer.py    # 行业分析
│   └── macro_analyzer.py       # 宏观分析
│
├── generators/                 # 报告生成层
│   ├── __init__.py
│   ├── chart_generator.py      # 多模态图表生成
│   ├── report_writer.py        # 结构化报告撰写
│   └── citation_checker.py     # 事实溯源校验
│
└── templates/                  # 报告模板
    ├── company_report.md       # 公司研报模板
    ├── industry_report.md      # 行业研报模板
    └── macro_report.md         # 宏观研报模板
```

### 3.2 SKILL.md 技能定义

```markdown
---
name: finance-report
version: 2.0.0
description: 金融分析与投资决策 Agent：在组委会指定上市公司池内选股并输出仓位配置，批量生成个股研报，深度耦合 JiuwenSwarm 框架（多智能体团队+Swarmflow+RAG+MCP）
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

## 使用方式

本技能通过技能内封装的 Python 模块采集数据与分析（经框架执行，不使用独立脚本绕过框架）。

### 生成公司研报

```bash
python workspace/agent/skills/finance-report/run_report.py company --target 600519 --name 贵州茅台 --save
```

### 投资决策（选股 + 仓位配置）

```bash
python workspace/agent/skills/finance-report/run_report.py invest --pool-file example/上市公司列表.xlsx --save
```

## ⚠️ 重要约束

1. **投资标的限定**：仅可在组委会公布的上市公司列表内选择；判定均无投资价值时可空仓，但须在报告中阐明决策逻辑
2. **事实溯源**：所有数据与论据必须标注来源，CitationChecker 会逐条校验
3. **禁止编造**：模型不得生成无法溯源的数据，无数据时应明确标注"暂无公开数据"
4. **图文一致**：图表与正文必须使用同一份数据，禁止图表与文字数据矛盾
5. **成果可复现**：决策过程与资源消耗须记录在案，第三方可凭代码与环境说明复现
6. **框架约束**：不得以未经封装的独立脚本绕过 openJiuwen 框架约束
```

### 3.3 研报结构模板（公司研报）

公司研报按 `股票代码.md` 命名，结构模板如下：

```markdown
# {{公司名称}}（{{股票代码}}）投资分析报告

| 属性 | 值 |
|------|-----|
| 报告类型 | 公司研报 |
| 评级 | 买入/增持/中性/减持 |
| 报告日期 | {{YYYY-MM-DD}} |
| 数据截止日 | {{YYYY-MM-DD}} |

## 一、核心观点
（3-5 条核心论点，每条附关键数据支撑）

## 二、投资结论与仓位建议
（评级与仓位建议（0-10% 权重）及决策逻辑；空仓时阐明不配置理由）

## 三、公司概况
### 3.1 公司简介
### 3.2 主营业务与股权结构

## 四、行业分析
### 4.1 行业景气度
### 4.2 竞争格局与公司地位（同板块公司横向对比）

## 五、财务分析
### 5.1 财务三表概览
（资产负债表 / 利润表 / 现金流量表关键科目表格）
### 5.2 盈利能力分析
（ROE/ROA/毛利率/净利率趋势图）
### 5.3 偿债能力与现金流
### 5.4 营运能力分析

## 六、估值分析
### 6.1 相对估值（PE/PB/PS 对比）
### 6.2 盈利预测

## 七、风险提示
（明确列示主要风险因素）

---
*数据来源：{{来源列表}}*
*免责声明：本报告由 AI Agent 自动生成，仅供参考，不构成投资建议。*
```

---

## 第四章｜多 Agent 协同设计

多 Agent 能力落地为 JiuwenSwarm 的 **Leader-Team 架构**：Leader 解析任务意图后动态组建专业团队，团队内各 Agent 角色边界明确，通过 Symphony 协议状态同步。本方案设计五个子 Agent（在原四角色基础上增设 **Investor 投资决策 Agent**，完成赛题核心的选股与仓位配置任务），由 Orchestrator 统一调度。

### 4.1 子 Agent 职责

| 子 Agent | 职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| **Planner** | 任务规划：判断研报类型，拆解子任务，制定采集计划 | 用户请求 | 任务计划 JSON |
| **Researcher** | 数据研究：迭代式 Deep Research 采集数据、RAG 检索、整理论据 | 任务计划 | 结构化数据 + 论据卡片 |
| **Writer** | 报告撰写：分治式（先大纲后分段）生成含图表的研报 | 结构化数据 | 研报 Markdown |
| **Reviewer** | 审查校验：事实溯源、逻辑审查、图文一致性检查 | 研报初稿 | 审查报告 + 是否通过 |
| **Investor** | 投资决策：基于研报结论评分选股、输出仓位权重 | 各公司研报结论 | `Portfolio.json`（支持满仓/半仓/空仓） |

### 4.2 编排入口核心代码

```python
# orchestrator.py
# -*- coding: utf-8 -*-
"""
多 Agent 编排器（封装为 JiuwenSwarm 技能模块，可由 Swarmflow 工作流调度）

通过链式推理调度五个子 Agent，完成端到端投资决策与研报生成。
包含自检与反馈循环：Reviewer 不通过则回流 Researcher/Writer 重做；
报告定稿后由 Investor 完成选股评分与仓位配置。
"""

from dataclasses import dataclass, field
from typing import Optional

from .agents.planner import PlannerAgent
from .agents.researcher import ResearcherAgent
from .agents.writer import WriterAgent
from .agents.reviewer import ReviewerAgent
from .agents.investor import InvestorAgent


@dataclass
class ReportRequest:
    """研报生成请求"""
    report_type: str          # company / industry / macro
    target: str               # 股票代码 / 行业名 / 时间周期
    name: str = ""            # 公司名称 / 行业名称
    period: str = ""          # 报告周期
    max_revision_rounds: int = 2  # 最大修订轮次


@dataclass
class ReportResult:
    """研报生成结果"""
    report_type: str
    target: str
    content: str = ""
    charts: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    passed_review: bool = False
    review_notes: str = ""
    portfolio: dict = field(default_factory=dict)  # 投资决策结果（股票代码→仓位权重）


class ReportOrchestrator:
    """研报生成编排器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.planner = PlannerAgent(config)
        self.researcher = ResearcherAgent(config)
        self.writer = WriterAgent(config)
        self.reviewer = ReviewerAgent(config)
        self.investor = InvestorAgent(config)

    def generate(self, request: ReportRequest) -> ReportResult:
        """端到端生成研报"""
        result = ReportResult(
            report_type=request.report_type, target=request.target
        )

        # 阶段1：任务规划（链式推理起点）
        plan = self.planner.plan(request)

        # 阶段2：数据研究
        research_data = self.researcher.research(plan)

        # 阶段3 + 4：撰写 + 审查（自检反馈循环）
        for round_idx in range(request.max_revision_rounds + 1):
            # 撰写报告
            draft = self.writer.write(research_data, request)

            # 审查校验
            review = self.reviewer.review(draft, research_data)

            result.content = draft.content
            result.charts = draft.charts
            result.citations = draft.citations
            result.passed_review = review.passed
            result.review_notes = review.notes

            if review.passed:
                break

            # 未通过：根据审查意见补充数据并重写
            if round_idx < request.max_revision_rounds:
                research_data = self.researcher.supplement(
                    research_data, review.feedback
                )

        # 阶段5：投资决策（选股评分 + 仓位配置，输出 Portfolio.json）
        if request.report_type == "company":
            result.portfolio = self.investor.decide(result)

        return result
```

### 4.3 审查 Agent 的反馈循环

Reviewer 是质量闸门，用"批判者"角色兑现迭代式精炼（草稿→批判→修改循环），直接保障决赛对报告完整性与逻辑严谨性的考察：

```python
# agents/reviewer.py
# -*- coding: utf-8 -*-
"""
审查校验 Agent

执行三类检查：
1. 事实溯源校验：每个数据/论据是否有引用，引用来源是否权威
2. 逻辑审查：论点-论据链是否完整，章节衔接是否流畅
3. 图文一致性：图表数据与正文数据是否一致
"""

from dataclasses import dataclass
from typing import List


@dataclass
class ReviewResult:
    """审查结果"""
    passed: bool
    score: float               # 0-100
    notes: str                 # 审查意见
    issues: List[str]          # 问题清单
    feedback: dict             # 反馈给上游的修改建议


class ReviewerAgent:
    """审查校验 Agent"""

    # 权威数据源白名单（用于溯源校验）
    AUTHORITATIVE_SOURCES = [
        "国家统计局", "上交所", "深交所", "港交所",
        "巨潮资讯网", "新浪财经", "财联社", "证券时报",
        "东方财富", "同花顺",
    ]

    def review(self, draft, research_data) -> ReviewResult:
        issues = []

        # 1. 事实溯源校验
        citation_issues = self._check_citations(draft)
        issues.extend(citation_issues)

        # 2. 图文一致性校验
        consistency_issues = self._check_chart_text_consistency(draft)
        issues.extend(consistency_issues)

        # 3. 结构完整性校验
        structure_issues = self._check_structure(draft)
        issues.extend(structure_issues)

        # 4. 合规性校验（风险提示等）
        compliance_issues = self._check_compliance(draft)
        issues.extend(compliance_issues)

        score = max(0.0, 100.0 - len(issues) * 10.0)
        passed = len(issues) == 0 and score >= 70.0

        return ReviewResult(
            passed=passed,
            score=score,
            notes=f"审查得分 {score:.1f}，发现 {len(issues)} 个问题",
            issues=issues,
            feedback={"issues": issues, "research_data": research_data},
        )

    def _check_citations(self, draft) -> List[str]:
        """事实溯源校验：检查数据是否标注来源"""
        issues = []
        for claim in draft.claims:
            if not claim.citation:
                issues.append(f"论据无来源: {claim.text[:30]}...")
            elif not any(
                src in claim.citation
                for src in self.AUTHORITATIVE_SOURCES
            ):
                issues.append(f"来源非权威: {claim.citation[:30]}...")
        return issues

    def _check_chart_text_consistency(self, draft) -> List[str]:
        """图文一致性校验"""
        issues = []
        for chart in draft.charts:
            for mention in chart.text_mentions:
                if abs(mention - chart.data_value) > 0.01:
                    issues.append(
                        f"图文不一致: {chart.title} "
                        f"图表值={chart.data_value} 正文值={mention}"
                    )
        return issues

    def _check_structure(self, draft) -> List[str]:
        """结构完整性校验"""
        issues = []
        required_sections = ["核心观点", "财务分析", "估值分析", "风险提示"]
        for section in required_sections:
            if section not in draft.content:
                issues.append(f"缺失必要章节: {section}")
        return issues

    def _check_compliance(self, draft) -> List[str]:
        """合规性校验"""
        issues = []
        if "风险提示" not in draft.content:
            issues.append("缺失风险提示章节（违反披露要求）")
        if "免责声明" not in draft.content:
            issues.append("缺失免责声明")
        if "数据来源" not in draft.content:
            issues.append("缺失数据来源标注")
        return issues
```

---

## 第五章｜数据采集层完整实现

所有数据由 Agent 自行从公开渠道采集，采集能力全部封装为 JiuwenSwarm 技能模块（不以独立脚本绕过框架）。

**采集范围与批量策略**：目标公司池为组委会公布的六大板块 A 股上市公司（见 `example/上市公司列表.xlsx`）。建议按板块批量采集：以板块为单位一次性拉取该板块所有公司的三大表与行情数据（akshare 对 A 股支持完善），天然形成同业对比数据集；同板块公司互为竞对，可直接做板块内两两对比，无需模型猜测竞对。

**迭代式 Deep Research**：单次搜索覆盖面不足，新闻与行业信息采集采用"搜索→分析→精炼→再搜"循环，直到信息饱和或达到深度上限（详见 5.2 节）。

### 5.1 行情数据采集器

```python
# collectors/quote_collector.py
# -*- coding: utf-8 -*-
"""
行情数据采集器

采集股票与指数的历史行情：收盘价、成交量、涨跌幅等。
数据来源：证券交易所公开数据 / 公开财经数据服务（免费）。
通过 MCP 工具调用外部行情接口，避免硬编码付费 API。
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional


@dataclass
class QuoteRecord:
    """行情记录"""
    date: str
    close: float
    volume: float
    change_pct: float

    def to_dict(self) -> dict:
        return {
            "date": self.date, "close": self.close,
            "volume": self.volume, "change_pct": self.change_pct,
        }


@dataclass
class QuoteData:
    """行情数据"""
    symbol: str
    name: str
    records: List[QuoteRecord] = field(default_factory=list)

    @property
    def latest_close(self) -> float:
        return self.records[-1].close if self.records else 0.0

    @property
    def period_return(self) -> float:
        """区间收益率"""
        if len(self.records) < 2:
            return 0.0
        return (self.records[-1].close / self.records[0].close - 1) * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "name": self.name,
            "latest_close": self.latest_close,
            "period_return": round(self.period_return, 2),
            "records": [r.to_dict() for r in self.records],
        }


class QuoteCollector:
    """行情数据采集器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def collect(
        self, symbol: str, name: str = "",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> QuoteData:
        """
        采集指定区间的行情数据

        通过 MCP 行情工具获取数据，确保来源公开免费。
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime(
                "%Y-%m-%d"
            )

        data = QuoteData(symbol=symbol, name=name)

        # 通过 MCP 工具调用行情接口（公开免费数据源）
        # 此处封装为可替换的采集策略，便于适配不同数据源
        records = self._fetch_via_mcp(symbol, start_date, end_date)
        data.records = records
        return data

    def collect_batch(self, symbols: List[tuple]) -> List["QuoteData"]:
        """按板块批量采集多只标的行情（公司池批量处理入口）"""
        return [self.collect(sym, name) for sym, name in symbols]

    def _fetch_via_mcp(
        self, symbol: str, start: str, end: str
    ) -> List[QuoteRecord]:
        """通过 MCP 工具获取行情（公开数据源）"""
        # 实际实现通过 mcp_tool_call 调用配置的行情 MCP 服务
        # 此处为接口示例，数据源需明确标注来源以保证可复现
        return []
```

### 5.2 新闻与政策采集器（迭代式 Deep Research）

新闻与政策采集是提升数据深度与广度的关键。单次关键词搜索覆盖面不足，本采集器实现**迭代式 Deep Research**：

1. **初始查询**：根据目标公司与板块生成几个核心搜索查询；
2. **结果分析**：提取关键实体、概念与新问题；
3. **查询精炼与扩展**：基于新信息生成更具体深入的查询（如发现"XX 新兴业务"→立即搜"XX 业务市场份额""主要竞争对手"）；
4. **循环与终止**：重复直到不再返回有价值新信息（信息饱和）或达到搜索深度上限。

```python
# collectors/news_collector.py
# -*- coding: utf-8 -*-
"""
新闻与政策采集器

采集主流财经媒体新闻与政府政策文件。
来源：新浪财经、财联社、证券时报、政府部门官网等公开渠道。
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class NewsItem:
    """新闻条目"""
    title: str
    source: str          # 来源媒体
    url: str             # 原文链接（用于溯源）
    date: str
    summary: str = ""
    sentiment: str = ""  # positive / neutral / negative

    def to_dict(self) -> dict:
        return {
            "title": self.title, "source": self.source, "url": self.url,
            "date": self.date, "summary": self.summary,
            "sentiment": self.sentiment,
        }


@dataclass
class NewsData:
    """新闻数据"""
    keyword: str
    items: List[NewsItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "count": len(self.items),
            "items": [i.to_dict() for i in self.items],
        }


class NewsCollector:
    """新闻与政策采集器"""

    # 权威来源白名单
    RELIABLE_SOURCES = [
        "新浪财经", "财联社", "证券时报", "上海证券报",
        "中国证券报", "经济日报", "人民日报", "新华网",
    ]

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def collect(
        self, keyword: str, limit: int = 20,
        max_depth: int = 3,
    ) -> NewsData:
        """迭代式 Deep Research 采集：搜索→分析→精炼→再搜，直到信息饱和"""
        data = NewsData(keyword=keyword)

        # 初始查询
        queries = self._initial_queries(keyword)
        seen_urls = set()

        for depth in range(max_depth):
            # 执行当前批查询
            for query in queries:
                for item in self._search_news(query, limit):
                    url = item.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if self._is_reliable(item.get("source", "")):
                        data.items.append(NewsItem(
                            title=item.get("title", ""),
                            source=item.get("source", ""),
                            url=url,
                            date=item.get("date", ""),
                            summary=item.get("summary", ""),
                        ))

            # 基于新信息精炼扩展查询；无新增有价值信息则信息饱和，终止
            new_queries = self._refine_queries(queries, data.items)
            if not new_queries:
                break
            queries = new_queries
        return data

    def _initial_queries(self, keyword: str) -> List[str]:
        """根据关键词生成初始查询集"""
        return [keyword, f"{keyword} 最新动态", f"{keyword} 政策"]

    def _refine_queries(
        self, queries: List[str], items: List[NewsItem]
    ) -> List[str]:
        """从已有结果提取新实体/新问题，生成下一轮更深入的查询；
        无新增有价值信息时返回空列表（信息饱和）"""
        return []

    def _is_reliable(self, source: str) -> bool:
        return any(s in source for s in self.RELIABLE_SOURCES)

    def _search_news(self, keyword: str, limit: int) -> List[dict]:
        """通过 MCP 搜索工具检索新闻（公开渠道）"""
        return []
```

### 5.3 公司披露采集器

```python
# collectors/filing_collector.py
# -*- coding: utf-8 -*-
"""
公司披露文件采集器

采集上市公司定期财报与临时公告。
来源：上交所、深交所、港交所信息披露平台 / 巨潮资讯网 / 公司官网。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FinancialStatement:
    """财务报表数据"""
    period: str                    # 报告期 YYYY-Qn
    # 利润表
    revenue: float = 0.0           # 营业收入
    net_profit: float = 0.0        # 净利润
    gross_profit: float = 0.0      # 毛利润
    # 资产负债表
    total_assets: float = 0.0      # 总资产
    total_liabilities: float = 0.0 # 总负债
    shareholders_equity: float = 0.0  # 股东权益
    # 现金流量表
    operating_cashflow: float = 0.0   # 经营现金流
    # 衍生指标
    gross_margin: float = 0.0      # 毛利率
    net_margin: float = 0.0        # 净利率
    roe: float = 0.0               # 净资产收益率
    debt_ratio: float = 0.0        # 资产负债率

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "revenue": self.revenue, "net_profit": self.net_profit,
            "gross_margin": round(self.gross_margin, 4),
            "net_margin": round(self.net_margin, 4),
            "roe": round(self.roe, 4),
            "debt_ratio": round(self.debt_ratio, 4),
        }


@dataclass
class FilingData:
    """公司披露数据"""
    symbol: str
    statements: List[FinancialStatement] = field(default_factory=list)
    announcements: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "statements": [s.to_dict() for s in self.statements],
            "announcements": self.announcements,
        }


class FilingCollector:
    """公司披露采集器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def collect(
        self, symbol: str, periods: int = 8
    ) -> FilingData:
        """采集最近 N 个报告期的财务数据"""
        data = FilingData(symbol=symbol)
        data.statements = self._fetch_financials(symbol, periods)
        data.announcements = self._fetch_announcements(symbol)
        return data

    def _fetch_financials(
        self, symbol: str, periods: int
    ) -> List[FinancialStatement]:
        """从交易所披露平台抓取财报数据（公开免费）"""
        return []

    def _fetch_announcements(self, symbol: str) -> List[dict]:
        """抓取临时公告"""
        return []
```

### 5.4 RAG 检索增强

RAG 用于解决"财务分析能力缺失"——把财务分析方法论、行业知识、历史研报沉淀为知识库，检索增强生成，为报告的逻辑严谨性提供专业知识支撑。

```python
# collectors/rag_retriever.py
# -*- coding: utf-8 -*-
"""
财务知识 RAG 检索器

将财务分析方法论、行业知识、估值模型说明等沉淀为向量知识库，
生成研报时检索相关知识片段，增强专业性与准确性，降低幻觉。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class KnowledgeChunk:
    """知识片段"""
    content: str
    source: str        # 来源文档
    score: float       # 相似度得分
    metadata: dict = field(default_factory=dict)


class RAGRetriever:
    """财务知识 RAG 检索器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        # 知识库目录：财务方法论、行业研究框架、估值模型等
        self.kb_dir = self.config.get(
            "kb_dir", "workspace/agent/memory/finance_kb"
        )

    def retrieve(
        self, query: str, top_k: int = 5
    ) -> List[KnowledgeChunk]:
        """
        检索相关知识片段

        Args:
            query: 检索 query（如"半导体行业景气度分析框架"）
            top_k: 返回数量
        """
        # 实现要点：
        # 1. 将 query 向量化（使用开源 Embedding 模型）
        # 2. 在向量库中检索 top_k 相似片段
        # 3. 返回带来源标注的知识片段
        return []

    def add_documents(self, docs: List[dict]) -> int:
        """向知识库添加文档（构建/更新阶段使用）"""
        count = 0
        for doc in docs:
            chunks = self._split_and_embed(doc)
            self._store_vectors(chunks)
            count += len(chunks)
        return count

    def _split_and_embed(self, doc: dict) -> List[KnowledgeChunk]:
        """切分文档并向量化"""
        return []

    def _store_vectors(self, chunks: List[KnowledgeChunk]):
        """存入向量库"""
        pass
```

### 5.5 混合记忆管理

公司池含数十家标的、数据源多、单标的分析链路长，若全量数据进上下文必然**记忆爆炸**。采用三层混合记忆系统（短期/长期/外部）分流管理：

| 记忆层 | 实现 | 在本方案的作用 |
| --- | --- | --- |
| **短期记忆** | LLM 上下文窗口 | 当前标的的分析上下文；大表格只给模型看**表头 + 前 5 行 + 后 5 行**，不全量打印 |
| **长期记忆** | 持久化存储（JiuwenSwarm 记忆系统） | 每家公司分析完成后，将结论与关键信息**摘要沉淀**，后续决策/报告环节以摘要形态复用 |
| **外部记忆** | 向量知识库（财务知识 RAG） | 财务方法论、行业框架、历史结论，按需检索 |

**混合机制**：实时判断信息分流——临时信息入短期、有长期价值的摘要后入长期；跨标的批量分析时，前序标的的结论以长期记忆摘要形态注入后续标的的上下文，既控制长度又保留板块横向对比信息。

---

## 第六章｜分析引擎层

分析引擎层将财务/行业/宏观分析方法论沉淀为可调用模块，避免模型"凭感觉"分析。对应赛题报告分析维度：基本面分析（财务/估值）、宏观经济分析、技术分析（行情指标）、另类数据挖掘与情绪因子（新闻与舆情数据）。

> **框架强制性落地**：分析能力均以 JiuwenSwarm 技能模块实现，CodeExecutor 与三个分析器都封装在技能目录内，不使用未经封装的独立脚本绕过框架。

### 6.1 财务分析器

```python
# analyzers/finance_analyzer.py
# -*- coding: utf-8 -*-
"""
财务分析器

对财务三表进行结构化分析：
- 盈利能力：毛利率、净利率、ROE、ROA
- 偿债能力：资产负债率、流动比率
- 营运能力：存货周转、应收周转
- 成长能力：营收增速、净利润增速
- 估值：PE、PB、PS
"""

from dataclasses import dataclass, field
from typing import List

from ..collectors.filing_collector import FinancialStatement


@dataclass
class FinanceAnalysis:
    """财务分析结果"""
    profitability: dict = field(default_factory=dict)  # 盈利能力
    solvency: dict = field(default_factory=dict)       # 偿债能力
    operation: dict = field(default_factory=dict)      # 营运能力
    growth: dict = field(default_factory=dict)         # 成长能力
    valuation: dict = field(default_factory=dict)      # 估值
    insights: List[str] = field(default_factory=list)  # 分析洞察

    def to_dict(self) -> dict:
        return {
            "profitability": self.profitability,
            "solvency": self.solvency,
            "operation": self.operation,
            "growth": self.growth,
            "valuation": self.valuation,
            "insights": self.insights,
        }


class FinanceAnalyzer:
    """财务分析器"""

    def analyze(
        self,
        statements: List[FinancialStatement],
        quote_data: dict = None,
    ) -> FinanceAnalysis:
        result = FinanceAnalysis()

        if not statements:
            result.insights.append("暂无公开财务数据")
            return result

        latest = statements[-1]

        # 盈利能力
        result.profitability = {
            "gross_margin": latest.gross_margin,
            "net_margin": latest.net_margin,
            "roe": latest.roe,
        }

        # 偿债能力
        result.solvency = {
            "debt_ratio": latest.debt_ratio,
        }

        # 成长能力（同比）
        if len(statements) >= 2:
            prev = statements[-2]
            if prev.revenue > 0:
                rev_growth = (latest.revenue / prev.revenue - 1) * 100
                result.growth["revenue_growth"] = round(rev_growth, 2)
            if prev.net_profit > 0:
                profit_growth = (
                    latest.net_profit / prev.net_profit - 1
                ) * 100
                result.growth["net_profit_growth"] = round(
                    profit_growth, 2
                )

        # 估值（需结合行情市值数据）
        if quote_data and latest.net_profit > 0:
            market_cap = quote_data.get("market_cap", 0)
            if market_cap > 0:
                result.valuation["pe"] = round(
                    market_cap / latest.net_profit, 2
                )

        # 生成分析洞察
        result.insights = self._generate_insights(result, statements)

        return result

    def _generate_insights(
        self, result: FinanceAnalysis,
        statements: List[FinancialStatement],
    ) -> List[str]:
        insights = []

        gm = result.profitability.get("gross_margin", 0)
        if gm > 0.5:
            insights.append(f"毛利率 {gm:.1%}，处于较高水平，议价能力强")
        elif gm < 0.2:
            insights.append(f"毛利率 {gm:.1%}，偏低，需关注成本控制")

        rev_g = result.growth.get("revenue_growth")
        if rev_g is not None:
            if rev_g > 20:
                insights.append(f"营收增速 {rev_g:.1f}%，成长性突出")
            elif rev_g < 0:
                insights.append(f"营收增速 {rev_g:.1f}%，出现下滑，需警惕")

        return insights
```

### 6.2 CodeExecutor 代码执行器

财务计算、指标对比、图表渲染等需要动态执行代码。CodeExecutor 基于 IPython 构建 **Notebook 式持久化、有状态执行环境**（封装为技能模块，由框架调度执行）：

- **持久化有状态**：变量与上下文在不同代码块间传递，适合多步财务分析（先取数、再算指标、再画图）
- **AST 静态分析 + 白名单机制**：只允许 `pandas/numpy/matplotlib` 等安全库导入，禁止 `exec/eval` 等高风险内置函数
- **预导入常用库、配置中文字体（SimHei）**，追踪新生变量，捕获 stdout/stderr，格式化 DataFrame 输出

```python
# analyzers/code_executor.py
# -*- coding: utf-8 -*-
"""
CodeExecutor：Notebook 式代码执行器

基于 IPython InteractiveShell 模拟持久化、有状态的执行环境，
让模型生成的分析代码在受控沙箱中执行，变量跨代码块保留。
"""

import ast
from typing import List, Tuple


class CodeExecutor:
    """Notebook 式代码执行器"""

    # 白名单：仅允许导入的安全库
    ALLOWED_IMPORTS = {"pandas", "numpy", "matplotlib", "datetime", "math"}
    # 禁止的内置函数
    BLOCKED_BUILTINS = {"exec", "eval", "compile", "__import__"}

    def __init__(self):
        self._shell = None  # IPython InteractiveShell 实例（懒初始化）

    def execute(self, code: str) -> Tuple[bool, str]:
        """执行单个代码块，状态在多次调用间保留

        Returns:
            (是否成功, 输出/错误信息)
        """
        if not self._is_safe(code):
            return False, "代码未通过 AST 白名单校验"
        shell = self._ensure_shell()
        # 在持久化 shell 中执行，捕获 stdout/stderr
        return self._run_in_shell(shell, code)

    def _is_safe(self, code: str) -> bool:
        """AST 静态分析：校验导入白名单与危险调用"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if not all(
                    alias.name.split(".")[0] in self.ALLOWED_IMPORTS
                    for alias in node.names
                ):
                    return False
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Name)
                    and func.id in self.BLOCKED_BUILTINS
                ):
                    return False
        return True

    def _ensure_shell(self):
        """懒初始化 IPython shell，预导入常用库、配置中文字体"""
        return self._shell

    def _run_in_shell(self, shell, code: str) -> Tuple[bool, str]:
        """在 shell 中执行代码块，返回执行结果与输出"""
        return True, ""
```

**上下文管理技巧**：大表格不全量打印给模型，只给**表头 + 前 5 行 + 后 5 行**，既让模型理解结构又不撑爆上下文窗口。

### 6.3 行业分析器与宏观分析器

行业分析器聚焦景气度、竞争格局、产业链；宏观分析器聚焦 GDP/CPI/PMI 等指标与政策趋势。两者结构与财务分析器类似，均输出结构化指标 + 分析洞察，此处不再赘述完整代码。行业分析中的竞对识别直接复用公司池的六大板块分组（同板块公司天然为竞对），做板块内两两对比。

---

## 第七章｜多模态报告生成层

报告质量直接影响决赛评分（完整性、逻辑严谨性），也是投资决策的载体。生成层负责图表生成、分治式结构化撰写与事实溯源。

### 7.1 分治式长报告生成

长篇研报不让模型一口气写完（质量难控、长度受限），采用**先大纲后分段**的分治策略：

1. **先生成 YAML 格式大纲**（part_title + part_desc），确定章节骨架；
2. **逐段生成**：遍历大纲，每段传入"前文 + 背景 + 原始材料"，用已生成内容反复喂回突破单次输出长度限制；
3. **严格图片引用规则**：只允许引用真实存在的本地图片，移除失效引用，保证报告自包含（可复现交付的前提）。

### 7.2 多模态图表生成器

```python
# generators/chart_generator.py
# -*- coding: utf-8 -*-
"""
多模态图表生成器

生成研报所需的各类图表：
- 股价/指数走势图（折线图）
- 财务指标对比图（柱状图）
- 财务报表表格（Markdown 表格）
- 估值对比图（条形图）

图表与正文使用同一份数据源，确保图文一致。
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Chart:
    """图表对象"""
    title: str
    chart_type: str         # line / bar / table / pie
    data: dict              # 图表数据（与正文同源）
    image_path: str = ""    # 生成的图片路径
    caption: str = ""       # 图注
    source: str = ""        # 数据来源（溯源）


class ChartGenerator:
    """图表生成器"""

    def __init__(self, output_dir: str = "workspace/agent/reports/finance-report/charts"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_price_chart(
        self, quote_data: dict, title: str = "股价走势"
    ) -> Chart:
        """生成股价走势图"""
        chart = Chart(
            title=title, chart_type="line",
            data=quote_data, source=quote_data.get("name", ""),
        )
        chart.image_path = self._render_line(
            quote_data.get("records", []), title
        )
        chart.caption = (
            f"数据来源：{quote_data.get('name', '公开行情数据')}；"
            f"区间收益率：{quote_data.get('period_return', 0)}%"
        )
        return chart

    def generate_finance_table(
        self, statements: List[dict], title: str = "财务数据概览"
    ) -> Chart:
        """生成财务数据表格（Markdown 表格也是多模态的一种）"""
        chart = Chart(
            title=title, chart_type="table",
            data={"statements": statements},
            source="公司定期财报",
        )
        chart.caption = "数据来源：交易所信息披露平台"
        return chart

    def generate_margin_chart(
        self, statements: List[dict], title: str = "盈利能力趋势"
    ) -> Chart:
        """生成毛利率/净利率趋势图"""
        chart = Chart(title=title, chart_type="bar", data={"statements": statements})
        chart.image_path = self._render_bar(statements, title)
        chart.caption = "数据来源：公司定期财报"
        return chart

    def _render_line(self, records: list, title: str) -> str:
        """渲染折线图（使用 matplotlib 开源库）"""
        # import matplotlib
        # ... 绘制并保存 PNG
        return ""

    def _render_bar(self, statements: list, title: str) -> str:
        """渲染柱状图"""
        return ""
```

### 7.3 结构化报告撰写器

```python
# generators/report_writer.py
# -*- coding: utf-8 -*-
"""
结构化报告撰写器

按研报模板生成 Markdown，确保：
- 论点-论据链完整
- 所有数据标注来源
- 图表与正文同源
- 含风险提示与免责声明（合规）
"""

from dataclasses import dataclass, field
from typing import List

from .chart_generator import Chart
from ..analyzers.finance_analyzer import FinanceAnalysis


@dataclass
class ReportDraft:
    """报告初稿"""
    content: str = ""
    charts: List[Chart] = field(default_factory=list)
    claims: list = field(default_factory=list)   # 论据卡片（含引用）
    citations: List[str] = field(default_factory=list)


class ReportWriter:
    """报告撰写器"""

    def write(
        self, research_data: dict, request
    ) -> ReportDraft:
        draft = ReportDraft()
        report_type = request.report_type

        if report_type == "company":
            draft = self._write_company(research_data, request)
        elif report_type == "industry":
            draft = self._write_industry(research_data, request)
        elif report_type == "macro":
            draft = self._write_macro(research_data, request)

        return draft

    def _write_company(
        self, data: dict, request
    ) -> ReportDraft:
        draft = ReportDraft()
        lines = []

        finance: FinanceAnalysis = data.get("finance_analysis")
        quote = data.get("quote_data", {})
        company_name = request.name or quote.get("name", "")

        # 标题与基本信息
        lines.append(f"# {company_name}（{request.target}）公司研究报告")
        lines.append("")
        lines.append("| 属性 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 报告类型 | 公司研报 |")
        lines.append(f"| 报告日期 | {request.period or '最新'} |")
        lines.append("")

        # 核心观点
        lines.append("## 一、核心观点")
        lines.append("")
        if finance and finance.insights:
            for insight in finance.insights:
                lines.append(f"- {insight}")
        lines.append("")

        # 财务分析
        if finance:
            lines.append("## 四、财务分析")
            lines.append("")
            lines.append("### 4.1 盈利能力")
            lines.append("")
            lines.append("| 指标 | 数值 |")
            lines.append("|------|------|")
            for k, v in finance.profitability.items():
                lines.append(f"| {k} | {v} |")
            lines.append("")

        # 风险提示（合规必需）
        lines.append("## 六、风险提示")
        lines.append("")
        lines.append("- 宏观经济波动风险")
        lines.append("- 行业竞争加剧风险")
        lines.append("- 政策变化风险")
        lines.append("")

        # 数据来源与免责声明
        lines.append("---")
        lines.append("*数据来源：交易所信息披露平台、公开财经数据*")
        lines.append("*免责声明：本报告由 AI Agent 自动生成，仅供参考，不构成投资建议。*")

        draft.content = "\n".join(lines)
        draft.charts = data.get("charts", [])
        draft.citations = data.get("citations", [])

        return draft
```

### 7.4 事实溯源校验器

事实溯源保障报告严谨性与数据可信度——文中的关键论据必须有据可依、来源权威可靠，这也是成果可复现性的基础。

```python
# generators/citation_checker.py
# -*- coding: utf-8 -*-
"""
事实溯源校验器

对报告中的每条数据与论据进行溯源校验：
1. 是否标注来源
2. 来源是否权威
3. 数据值是否与来源一致
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class CitationCheck:
    """溯源校验结果"""
    total_claims: int = 0
    cited_claims: int = 0
    authoritative_claims: int = 0
    issues: List[str] = field(default_factory=list)

    @property
    def citation_rate(self) -> float:
        if self.total_claims == 0:
            return 0.0
        return self.cited_claims / self.total_claims

    @property
    def passed(self) -> bool:
        return self.citation_rate >= 0.9 and len(self.issues) == 0


class CitationChecker:
    """事实溯源校验器"""

    AUTHORITATIVE_SOURCES = [
        "国家统计局", "上交所", "深交所", "港交所",
        "巨潮资讯网", "新浪财经", "财联社", "证券时报",
    ]

    def check(self, claims: List[dict]) -> CitationCheck:
        result = CitationCheck(total_claims=len(claims))

        for claim in claims:
            citation = claim.get("citation", "")
            if citation:
                result.cited_claims += 1
                if any(s in citation for s in self.AUTHORITATIVE_SOURCES):
                    result.authoritative_claims += 1
                else:
                    result.issues.append(
                        f"来源非权威: {claim.get('text', '')[:30]}"
                    )
            else:
                result.issues.append(
                    f"论据无来源: {claim.get('text', '')[:30]}"
                )

        return result
```

---

## 第八章｜配置与部署

### 8.1 环境变量配置（.env）

```env
# 模型配置（开源模型，OpenAI 兼容接口）
# 采用开源模型（Qwen3 / DeepSeek），保证环境可自主部署、成果可复现
MODEL_PROVIDER="OpenAI"
MODEL_NAME="Qwen/Qwen3-235B-A22B-Instruct-2507"
API_BASE="https://api-inference.modelscope.cn/v1"
API_KEY="your-api-key"

# Embedding 配置（RAG 向量化，使用开源 Embedding 模型）
EMBED_MODEL="bge-large-zh"
EMBED_API_BASE="http://localhost:8080"

# MCP 工具配置（行情查询、新闻搜索等外部工具）
# 在 config.yaml 中配置 MCP 服务端

# 飞书推送（可选）
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

### 8.2 应用配置（config/config.yaml）

```yaml
react:
  agent_name: main_agent
  max_iterations: 150            # 研报生成任务复杂，适当提高
  model_name: ${MODEL_NAME:-Qwen/Qwen3-235B-A22B-Instruct-2507}
  model_client_config:
    client_provider: OpenAI
    api_base: https://api-inference.modelscope.cn/v1
    api_key: ${API_KEY}
    verify_ssl: false
  context_engine_config:
    enable_reload: true
  evolution:
    skill_evolution: false
    auto_save: false

tools:
  - todo
  - skill

# MCP 工具服务配置（行情查询、新闻搜索、公告检索）
mcp:
  servers:
    - name: finance-data
      command: python
      args: ["-m", "mcp_servers.finance_data_server"]

# 心跳配置（定时生成研报）
heartbeat:
  every: 3600
  target: feishu
  active_hours:
    start: 17:00
    end: 18:00

# 投资决策 Agent 配置（选股与仓位配置）
investor:
  max_weight_per_stock: 0.4        # 单标的最大权重（风险控制约束）
  min_position_count: 3            # 建议最少持仓标的数（分散度约束）
  allow_empty_position: true       # 允许空仓（须在报告中阐明决策逻辑）
  portfolio_output: Portfolio.json # 提交格式：{"股票代码": 持仓占比}

channels:
  feishu:
    app_id: cli_xxx
    app_secret: xxx
    enabled: true
```

### 8.3 依赖安装

```bash
# 核心依赖
pip install matplotlib      # 图表生成（开源）
pip install jieba           # 中文分词（关键词提取，可选）

# RAG 向量检索（开源方案）
pip install chromadb        # 向量数据库
pip install sentence-transformers  # 开源 Embedding 模型

# 数据采集（公开数据源工具）
pip install requests beautifulsoup4 lxml

# 财务分析
pip install pandas numpy
```

### 8.4 心跳配置（HEARTBEAT.md）

```markdown
# 心跳任务

## 活跃的任务项

<!-- 定时生成研报任务 -->
- 生成今日关注的自选股公司研报
- 每周五生成行业研报
- 每月末生成宏观月度研报
```

---

## 第九章｜测试验证

| 测试方式 | 说明 |
|--------|--------|
| 命令行测试 | 直接执行入口脚本验证全流程 |
| 飞书对话 | 通过飞书机器人发送研报生成请求 |
| Web 对话 | 通过 JiuwenSwarm Web 界面交互 |

### 9.1 命令行测试

```bash
cd D:\Download\jiuwenswarm

# 生成公司研报（公司池内指定标的，输出 600519.md）
python workspace/agent/skills/finance-report/run_report.py company \
  --target 600519 --name 贵州茅台 --save

# 生成行业研报
python workspace/agent/skills/finance-report/run_report.py industry \
  --name 半导体 --save

# 投资决策（公司池选股 + 仓位配置，输出 Portfolio.json）
python workspace/agent/skills/finance-report/run_report.py invest \
  --pool-file example/上市公司列表.xlsx --save
```

### 9.2 飞书/Web 对话测试

在飞书私聊机器人或 Web 界面发送：

```
生成贵州茅台（600519）的投资分析报告
```

Agent 收到后自动：
1. Planner 判断为公司研报，拆解采集与分析子任务
2. Researcher 迭代式 Deep Research 采集行情、财报、新闻，RAG 检索财务知识
3. FinanceAnalyzer 通过 CodeExecutor 执行财务分析与估值代码
4. ChartGenerator 生成走势图与财务表格
5. ReportWriter 分治式（先大纲后分段）撰写结构化报告
6. Reviewer 校验溯源、图文一致性、合规性，不通过回流重写
7. 通过后保存为 `reports/finance-report/600519.md`；触发投资决策时由 Investor 输出 `Portfolio.json`

### 9.3 交付自检清单

对照比赛要求，交付前自检：

| 要求 | 自检项 |
|--------|--------|
| 初赛：收益率与风险控制 | Portfolio.json 格式正确（代码: 权重），单标的权重上限与分散度约束生效，决策逻辑留痕 |
| 决赛：报告完整性 | 个股报告结构齐全（核心观点/投资结论/财务分析/估值/风险提示/数据来源） |
| 决赛：逻辑严谨性 | Reviewer 自检反馈循环通过，图文一致性校验通过，论点-论据链完整 |
| 框架强制性 | 全部能力封装为 JiuwenSwarm 技能/团队/Swarmflow，无独立脚本绕过 |
| 成果可复现性 | 决策过程日志与资源消耗记录完整，附代码与环境说明 |
| 投资标的限定 | 选股均落在组委会公布列表内；空仓时阐明理由 |
| 提交格式 | `Portfolio.json` + `个股投资研报/股票代码.md` 批量产出 |

---

## 第十章｜技术要点总结

### 10.1 关键技术决策

| 决策点 | 选择 | 原因 |
| --- | --- | --- |
| 模型 | 开源模型（Qwen3/DeepSeek） | 环境可自主部署，保证成果可复现 |
| 框架 | 深度耦合 JiuwenSwarm（技能/团队/Swarmflow） | 赛题框架强制性要求，不得用独立脚本绕过 |
| 代码执行 | CodeExecutor（Notebook 式 + AST 白名单） | 财务计算/图表生成动态代码安全执行 |
| 多 Agent | Leader-Team：Planner/Researcher/Writer/Reviewer/Investor | 链式推理 + 自检反馈循环 + 投资决策闭环 |
| 数据深度 | 迭代式 Deep Research 至信息饱和 | 单次搜索覆盖面不足 |
| 记忆 | 三层混合记忆 + 结论摘要沉淀 | 避免公司池批量分析时记忆爆炸 |
| 检索增强 | RAG + 向量库 | 补足财务专业知识，降低幻觉 |
| 工具协议 | MCP | 标准化接入行情/新闻/公告工具 |
| 报告生成 | 分治式（先大纲后分段）+ 图片本地化 | 长报告稳定产出、交付自包含可复现 |
| 投资决策 | Investor Agent + 风控约束（仓位上限/分散度） | 初赛收益率与风控指标直接决定晋级 |
| 溯源 | CitationChecker 逐条校验 | 报告严谨性与可复现性基础 |

### 10.2 踩坑与规避

1. **模型幻觉编造数据**
   问题：开源模型可能编造无法溯源的财务数据。
   解决：CitationChecker 强制校验，无来源数据标注"暂无公开数据"，Reviewer 不通过则回流重写。

2. **图文数据不一致**
   问题：图表用一份数据，正文文字写另一份数据。
   解决：图表与正文共用同一数据对象，Reviewer 校验 `chart.data_value` 与 `text_mentions` 一致。

3. **标的越界选择**
   问题：模型可能推荐组委会列表之外的上市公司，违反投资标的限定。
   解决：Planner/Investor 均以公司池列表（`example/上市公司列表.xlsx`）为白名单硬校验，列表外代码直接剔除并在决策日志中留痕。

4. **泛化能力不足**
   问题：针对特定公司/行业做硬编码优化，换公司就失效。
   解决：分析逻辑参数化，研报类型与公司信息作为请求参数传入，框架不绑定特定标的。

5. **生成效率过低**
   问题：多 Agent 链式调用 + RAG 检索导致耗时过长。
   解决：合理设置 `max_revision_rounds`（默认 2 轮），Reviewer 快速失败减少无效重写。

6. **WebSocket 消息过大**
   问题：研报含多张图片，直接返回导致消息超限。
   解决：SKILL.md 约定只返回研报摘要与图表列表，完整研报保存到本地文件并告知路径。

7. **长报告一次性生成质量不稳**
   问题：让模型一口气写完整篇研报，每次输出结构与质量差异很大。
   解决：分治式生成（先 YAML 大纲后逐段撰写），每段传入前文上下文，输出稳定性显著提升。

8. **公司池批量分析记忆爆炸**
   问题：数十家标的全量数据进上下文，超出窗口报错。
   解决：三层混合记忆分流 + 大表格"头尾各 5 行"压缩 + 分析结论摘要沉淀长期记忆。

9. **仓位过度集中**
   问题：决策偏好单一高分标的，回撤风险大，初赛风控指标吃亏。
   解决：Investor 设单标的权重上限与分散度约束（见 8.2 investor 配置），空仓决策须阐明逻辑。

10. **决策不可复现**
    问题：第三方无法重放决策过程，违反成果可复现性要求。
    解决：记录完整决策日志与资源消耗（Swarmflow 运行树），附代码与环境说明，固定随机种子。

### 10.3 扩展方向

1. **RAG 三件套升级**：混合分块（父块保语义/子块精检索）+ 混合检索（向量 + BM25）+ 重排，进一步提升事实依据质量、降低幻觉
2. **Swarmflow 人工介入点**：在投资决策关键阶段设 HITL 审核点，结合 Token 预算控制保证流程可控
3. **情绪因子构建**：基于新闻情绪标注构建情绪因子，纳入 Investor 评分（赛题认可的分析维度）
4. **盈利预测模型**：接入时间序列预测，增强"盈利预测"章节
5. **增量研报**：基于上次研报仅更新变更部分，提升公司池批量产出效率
6. **历史决策复盘**：记录历史组合表现，追踪收益率与风控指标，反哺决策策略

---

## 写在最后

金融分析 Agent 的核心难点，不在于"写得像"，而在于"投得对、说得清"——数据真实可溯源、分析专业有深度、决策可复现。初赛只看收益率与风险控制指标，意味着投资决策环节的优先级不低于报告生成；决赛看报告完整性与逻辑严谨性，意味着质量兜底机制不可缺。这正是多 Agent 系统的价值所在：用 Leader-Team 分工把采集、分析、撰写、审查、决策拆开，用 RAG 与 CodeExecutor 补足专业能力，用混合记忆控制上下文，用反馈循环兜住质量。

JiuwenSwarm 的技能系统 + Swarmflow 确定性工作流 + Leader-Team 多智能体协同，为这套架构提供了现成的脚手架，也天然满足赛题"深度耦合 openJiuwen 框架"的强制要求——所有能力均封装为框架内模块，让我们能把精力集中在金融专业逻辑与投资决策本身，而非重复造轮子。

> **让 Agent 做出一份经得起复现的投资决策，从深度耦合框架与多 Agent 协同开始。**

---

**项目地址**：`workspace/agent/skills/finance-report/`

**技能文档**：`SKILL.md`

**入口脚本**：`run_report.py`

**编排入口**：`orchestrator.py`
