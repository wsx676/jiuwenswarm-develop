# finance-report — 金融分析与投资决策 Agent

> 华为 openJiuwen 赛题二参赛项目：基于 JiuwenSwarm 的 Agent 金融分析报告生成
>
> 在组委会指定的上市公司池（A 股六大板块、49 个标的）内**自主选股**，输出投资组合配置（`Portfolio.json`）与个股投资研报（`股票代码.md`），全链路可溯源、可复现。

---

## 一、核心能力

| 能力 | 说明 |
| --- | --- |
| Leader-Team 多智能体协作 | Planner / Researcher / Writer / Reviewer / Investor 五类 Agent 分工编排，自检反馈循环（审查不通过回流重写 ≤ 2 轮） |
| Swarmflow 五阶段确定性工作流 | 选股 → 采集 → 分析 → 决策 → 报告，阶段间以落盘产物传递状态（`data/` → `scores_cache.json` → `Portfolio.json` → 研报） |
| 迭代式 Deep Research 采集 | 行情三级降级链（东方财富 → 腾讯 → 新浪）、财报披露口径取数、新闻多源迭代检索（搜狗 → 新浪滚动 → Bing，max_depth=3 + 信息饱和判断） |
| RAG 财务知识库 | 13 篇种子方法论，智谱 embedding-3 向量化（MiniMax Key 不支持 embedding 时的替代方案），缺失时自动降级本地 TF-IDF（零依赖、可离线复现） |
| CodeExecutor 安全分析 | AST 白名单沙箱（仅 pandas/numpy/matplotlib）+ 动态访问原语拦截，财务/行业/宏观分析代码安全执行 |
| 质量内环 | CitationChecker 引用闸门（引用率 ≥ 90%）+ 权威来源白名单 + 图文同源校验 + Reviewer 四类校验（溯源/图文一致/结构/合规） |
| 确定性决策 | 因子打分与仓位分配为纯规则（无 LLM、无随机源），固定种子 `SEED=20260819`，两次运行评分完全一致 |

## 二、系统架构

```
                        Swarmflow 五阶段工作流（scripts/workflow.py）
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1 选股    │ → │ 2 采集    │ → │ 3 分析    │ → │ 4 决策    │ → │ 5 报告    │
│ pool     │   │ research │   │ research │   │ invest   │   │ company  │
│ 公司池校验 │   │ --stage  │   │ --stage  │   │ 评分+仓位 │   │ 入选标的  │
│ 板块枚举  │   │ collect  │   │ analyze  │   │ 分配      │   │ 逐个研报  │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
                    ↓               ↓               ↓               ↓
               data/ 缓存      scores_cache    Portfolio.json   个股投资研报/
               (49 标的×3类)      .json         + decision_log   {代码}.md
```

多智能体编排（单标的研报全流程）：

```
Planner(计划拆解) → Researcher(Deep Research 采集 + RAG 检索)
                  → Analyzers(财务/行业/宏观 + ChartGenerator 图文同源)
                  → Writer(YAML 大纲 → 分治式逐段撰写)
                  → Reviewer(溯源/图文/结构/合规校验) ⇄ 回流重写 ≤ 2 轮
                  → Investor(因子评分 → Portfolio 配置)
```

## 三、目录结构

```
finance-report/
├── SKILL.md                    # 技能定义（frontmatter + 触发词 + 使用方式）
├── README.md                   # 项目说明（本文件）
├── run_report.py               # CLI 入口（pool/research/company/industry/macro/invest）
├── orchestrator.py             # 多 Agent 编排（阶段计时 + 批量容错 + 重试留痕）
├── scripts/
│   └── workflow.py             # Swarmflow 五阶段确定性工作流（本技能执行定义）
├── agents/                     # 智能体层
│   ├── planner.py              # 任务规划（报告类型识别 + 子任务拆解）
│   ├── researcher.py           # 迭代式 Deep Research + RAG + 混合记忆
│   ├── writer.py               # 分治式报告撰写
│   ├── reviewer.py             # 四类校验 + 评分
│   └── investor.py             # 因子打分 + 仓位分配 + Portfolio 校验
├── collectors/                 # 数据采集层（行情/财报/新闻/公司池，全部带降级链）
├── analyzers/                  # 分析引擎层（CodeExecutor 沙箱执行）
├── generators/                 # 报告生成层（图表/结构化撰写/引用校验）
├── common/                     # 遥测（阶段耗时/Token/种子）+ LLM 客户端
├── templates/                  # 报告模板
└── example/                    # 赛题材料（上市公司列表.xlsx / 提交样例）
```

运行产物统一落盘项目根 `reports/finance-report/`（详见其目录下 `README.md`）。

## 四、环境要求与配置

- **Python ≥ 3.10**；核心依赖已在项目根 `pyproject.toml` 声明：
  `akshare pandas numpy matplotlib openpyxl requests ipython`
- 可选（RAG 向量化增强）：`chromadb sentence-transformers`；未安装自动降级本地 TF-IDF

项目根 `.env`（不随代码提交）：

| 变量 | 用途 | 缺省行为 |
| --- | --- | --- |
| `API_KEY` / `ANTHROPIC_API_KEY` | MiniMax LLM（Anthropic 协议，默认 `MiniMax-M2`） | 缺失时撰写/审查走规则降级，全流程仍可跑通 |
| `ZHIPU_API_KEY` | 智谱 embedding-3（RAG 向量化主路径） | 缺失时降级本地 TF-IDF |

## 五、快速开始

> 以下命令均在技能目录执行：`cd jiuwenswarm/resources/agent/workspace/skills/finance-report`

### 5.1 一键复现（首选入口）

赛题要求第三方可完整复现投资决策过程及资源消耗数据，提供一键脚本：

```bash
# 项目根目录执行：池校验 → 全池采集 → 因子打分 → Portfolio 决策
python jiuwenswarm/resources/agent/workspace/skills/finance-report/scripts/reproduce.py
#   --sector 消费板块   仅复现单板块（更快）
#   --with-report       末尾追加一份示例个股研报（贵州茅台）
```

采集缓存优先、幂等可重跑；脚本末尾打印产物摘要。资源消耗数据（各阶段耗时/LLM Token/固定种子/失败留痕）见 `reports/finance-report/decision_log/run_stats.json`；仓位决策逻辑（满仓/半仓/空仓的理由）见 `decision_log/decision.json` 的 `position_decision` / `position_rationale` 字段。

### 5.2 五阶段分步执行（与 Swarmflow 工作流等价）

```bash
python run_report.py pool                                       # 1 选股：公司池白名单校验与板块枚举
python run_report.py research --stage collect --save            # 2 采集：全池数据落盘（缓存优先、断点续采）
python run_report.py research --stage analyze --save            # 3 分析：因子评分 → scores_cache.json（不渲染图表）
python run_report.py invest --pool-file example/上市公司列表.xlsx \
    --use-cached-scores --max-positions 8 --skip-reports --save # 4 决策：Portfolio.json + 决策日志
python run_report.py company --target 603986 --save             # 5 报告：入选标的逐个生成研报
```

### 5.3 常用单命令

```bash
# 单标的研报（端到端：采集 → 分析 → 撰写 → 审查回流）
python run_report.py company --target 600519 --name 贵州茅台 --save

# 单板块决策（实时评分，不用缓存）
python run_report.py invest --sector 消费板块 --save

# 空仓决策合法：全部标的评分低于阈值时 Portfolio.json 为 {}，决策日志阐明理由
```

### 5.4 Swarmflow 工作流

经 JiuwenSwarm 框架调度 `scripts/workflow.py` 即可一键完成五阶段全流程（含阶段失败自动重试与降级路径），执行定义与上述 CLI 分步完全等价。

## 六、交付产物（赛题二提交格式）

产物位于项目根 `reports/finance-report/`，提交件格式与 `example/赛题二提交样例/` 一致：

```
Portfolio.json          # {"股票代码": 权重} 平铺映射（总权重 ≤ 1.0，单标的 ≤ 0.4）
个股投资研报/
└── {股票代码}.md        # 入选标的一个代码一份研报（八章节结构 + 图文同源）
```

**当前交付状态**：Portfolio 持仓 8 只（603986 / 601168 / 601899 / 300750 / 600426 / 300308 / 600309 / 601600，总权重 0.99，全部在公司池白名单内），8 份入选研报审查得分均 100 分。

辅助留痕（非提交件，供溯源与复现）：`decision_log/`（决策日志/评分缓存/运行遥测）、`data/`（采集缓存）、`charts/`（正文引用图表）。

## 七、质量保障

| 机制 | 说明 |
| --- | --- |
| 单元测试 | `tests/unit_tests/finance/` 18 个文件 **238 passed**（全离线：缓存预置/monkeypatch，不触网） |
| SwarmFlow 校验器 | swarmskill-creator 校验 **PASS**（0 error） |
| 事实溯源 | CitationChecker 段落级引用闸门（≥ 90%）+ 权威来源白名单，禁止编造数据 |
| 图文一致 | 图表与正文消费同一份数据 dict，Reviewer 校验图片本地存在且引用有效 |
| 提交硬约束 | Investor `validate_portfolio`：白名单内选股、单标的 ≤ 0.4、总权重 ≤ 1.0，越界即抛错 |
| 失败留痕 | 单标的失败三通道记录（决策日志 notes / run_stats.failures / logger），批量失败不阻断 |

## 八、可复现性

- **确定性规则**：因子打分与仓位分配无 LLM、无随机源（打分表见 `agents/investor.py` 注释）；进程启动即 `fix_random_seed(SEED=20260819)` 并记入 `run_stats.json`
- **状态传递**：采集缓存 `data/`（断点续采）→ 评分缓存 `scores_cache.json` → 决策 → 仅为入选标的生成报告，批量成本随阶段推进收敛
- **遥测记录**：`decision_log/run_stats.json` 滚动保留最近 10 次运行（各阶段耗时 / LLM 调用次数 / input·output token / 失败记录 / 种子）
- **复现验证**：实测两次独立运行分析阶段评分完全一致；第三方按第四节配置 + 第五节命令即可重放决策（评分差异只可能来自数据源时间点差异，`collected_at` 已留痕）

## 九、合规声明

- 仅使用组委会公布的上市公司列表内标的；无投资价值时支持空仓，并在决策日志阐明逻辑
- 所有数据与论据标注来源，财务指标取披露口径（不自行计算衍生值）
- 本 Agent 产出仅供比赛评审与技术研究，不构成任何投资建议
