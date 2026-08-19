# finance-report —— Agent 金融分析报告生成技能

华为 openJiuwen / CCF BDCI 2026【基于 JiuwenSwarm 的 Agent 金融分析报告生成】
（赛题二）参赛技能：多 Agent 协作，端到端生成**公司 / 行业 / 宏观**三类投资研报
与 `Portfolio.json` 投资决策，全流程可复现、可溯源、可审查。

## 核心能力

| 能力 | 说明 |
|------|------|
| 公司研报（八章） | 核心观点/投资结论与仓位建议/公司概况/行业/财务/估值/风险/来源，图文同源 |
| 行业研报（八章） | 板块景气度（新闻情绪+政策信号）+ 成分公司竞对横向对比 + 净利润 Top5 对比图 |
| 宏观研报（七章） | GDP/CPI/PMI 指标 + 政策动向 + 对六大板块的配置建议 |
| 投资决策 | 因子打分（财务/成长/估值/动量/风控）选股 + 仓位配置，输出 `Portfolio.json` |
| SwarmFlow 工作流 | 选股→采集→分析→决策→报告 五阶段确定性编排（`scripts/workflow.py`） |

## 多 Agent 架构

```plain
Planner（任务规划）→ Researcher（采集/分析/RAG）→ Writer（分治式撰写）
        ↑__________________ Reviewer（审查回流重写 ≤2 轮）
Investor（因子打分 + 仓位决策）
```

- **Planner**：判断研报类型、拆解采集/分析子任务、公司池白名单校验与板块竞对提取
- **Researcher**：行情/财报/新闻迭代式采集（缓存优先、断点续采）+ 财务/行业/宏观分析引擎 + RAG 方法论知识检索
- **Writer**：先大纲后逐段的分治式生成，前文摘要喂回突破长度限制；LLM 优先、规则模板兜底
- **Reviewer**：事实溯源（引用率 ≥90% 闸门）/图文一致/结构完整/合规 四类校验，不过则定向补采+回流重写
- **Investor**：五因子打分 + Top-N 选股 + 仓位配置（满仓/半仓/空仓均阐明理由）

## 目录结构

```plain
finance-report/
├── SKILL.md                    # 技能定义（触发词/任务表/执行流程）
├── run_report.py               # CLI 入口（company/industry/macro/invest/pool/research）
├── orchestrator.py             # 编排器（五阶段 + 审查回流 + 产物落盘）
├── agents/                     # 五个 Agent（planner/researcher/writer/reviewer/investor）
├── collectors/                 # 采集层（行情/财报/新闻/公司池，缓存优先）
├── analyzers/                  # 分析引擎（财务/行业/宏观 + CodeExecutor）
├── generators/                 # 生成层（report_writer/chart_generator/citation_checker）
├── common/                     # LLM 客户端、遥测（run_stats）、混合记忆
└── scripts/workflow.py         # SwarmFlow 五阶段工作流脚本
```

产物统一落盘项目根 `reports/finance-report/`：

```plain
reports/finance-report/
├── Portfolio.json              # 提交格式：{"股票代码": 权重}
├── 个股投资研报/{六位代码}.md    # 公司研报（提交目录）
├── 行业研报/industry_{板块}.md  # 行业研报
├── 宏观研报/macro_{周期}.md     # 宏观研报
├── charts/                     # 图文同源图表 PNG
├── data/                       # 采集缓存（可复现数据资产，49 只全池）
└── decision_log/               # 决策日志 + scores_cache + run_stats.json
```

## 快速开始

### 一键复现（首选入口，赛题复现要求）

```bash
# 端到端复现投资决策全过程：池校验→全池采集→因子打分→Portfolio 决策
# （采集缓存优先、幂等可重跑；资源消耗见 decision_log/run_stats.json）
python jiuwenswarm/resources/agent/workspace/skills/finance-report/scripts/reproduce.py
#   --sector 消费板块   仅复现单板块（更快）
#   --with-report       末尾追加一份示例个股研报
```

产物：`Portfolio.json` + `decision_log/decision.json`（含 `position_decision`
与 `position_rationale`，阐明满仓/半仓/空仓决策逻辑）+ `run_stats.json`。

### 分步执行

```bash
# 公司研报（单只）
python run_report.py company --target 600519 --name 贵州茅台 --save

# 行业研报（板块名须为公司池板块：消费板块/金融板块/科技/AI/半导体板块/
# 新能源/电力板块/周期/资源板块/高端制造/基建板块）
python run_report.py industry --name 消费板块 --save

# 宏观研报（季度周期）
python run_report.py macro --period 2026Q2 --save

# 投资决策（全池打分选股 + Portfolio.json）
python run_report.py invest --pool-file example/上市公司列表.xlsx --save

# SwarmFlow 五阶段工作流（批量：采集→分析→决策→报告）
python run_report.py research --stage collect
python run_report.py research --stage analyze --save
python run_report.py invest --pool-file example/上市公司列表.xlsx --use-cached-scores --skip-reports --save
```

公司池来源：组委会公布的 `example/上市公司列表.xlsx`（6 板块 49 只）。

## 质量保障

- **事实溯源**：正文数据句引用率 ≥90% 闸门 + 论据卡片权威白名单校验（CitationChecker）
- **图文同源**：图表与正文取同一份数据源，Reviewer 校验关键数值一致
- **可复现**：采集缓存落盘（复跑不重复请求）+ 种子固定 + `run_stats.json` 阶段耗时/Token/失败留痕
- **审查回流**：Reviewer 不过 → 定向补采 + 问题清单注入重写（≤2 轮），2 轮未过按当前稿放行留痕
- **测试**：`tests/unit_tests/finance/` 覆盖采集降级链、因子打分、审查闸门、批量容错、
  工作流结构合规与行业/宏观端到端（241 用例）

## 提交格式对齐

赛题二提交物 = `Portfolio.json`（平铺 `{"股票代码": 权重}`）+ `个股投资研报/{六位代码}.md`，
与 `example/赛题二提交样例` 目录结构一致；行业/宏观研报作为分析深度补充材料。
