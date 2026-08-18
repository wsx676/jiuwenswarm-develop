# JiuwenSwarm 全项目深度解读

> 解读日期：2026-08-18
> 解读范围：仓库 HEAD（`27694f2`）+ 本次拉取的全部 9 个新提交（Day 1–3 金融 skill 演进 + 评审修复）
> 解读深度：架构级（what / why / how,不到逐行）
> 输出位置：`docs/zh/开发实践/JiuwenSwarm-全项目深度解读.md`
> 适用读者：第一次接触 JiuwenSwarm 的工程师 / 评审者 / 想要基于此框架二次开发的人

---

## 0. 一句话总结

**JiuwenSwarm 是一个以"声明式 swarm 装配 + Leader-Team 多 Agent 协作 + Skill 自进化"为核心的分布式智能体框架。** 在其上，已经落地了一个完整、可投产的 **金融分析报告生成 skill（Day 1–3 全量交付 100 分）**，展示了"框架 + 真实业务 skill"的最佳实践。

| 维度 | 数据 |
|---|---|
| Python 源文件（核心） | 829 个 / 约 28.9 万行（不含 frontend/node_modules）|
| 测试文件 | 337 个 / 约 12.2 万行（测试:核心 ≈ 0.42:1,由核心框架承担质量主体）|
| 已注册 skill 目录 | 22 个（含 finance-report / skill-omni-creation / project-maintainer 等）|
| 内置命令（`[project.scripts]`） | 13 个入口（CLI / APP / AgentServer / Gateway / Web / TUI / ACP…）|
| 频道集成 | 9 个 IM 平台 + Web + TUI + Desktop |
| Python 版本 | ≥ 3.11 < 3.14 |
| 协议依赖 | OpenAI 兼容、ACP（Agent Connect Protocol）、A2A、E2A、FastAPI/uvicorn、WebSocket |
| 许可 | Apache-2.0 |

---

## 1. 项目全景图

### 1.1 三层 + 一工具

```text
┌─────────────────────────────────────────────────────────────────────┐
│                      入口 / 命令行层（13 个 console_scripts）         │
│   jiuwenswarm / jiuwenswarm-app / jiuwenswarm-start / -stop /        │
│   jiuwenswarm-init / jiuwenswarm-web / jiuwenswarm-gateway /         │
│   jiuwenswarm-agentserver / jiuwenswarm-tui / -desktop / -acp …      │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Gateway / Channel 层                           │
│   gateway/channel_manager/                                          │
│     ├─ im_platforms/  飞书/钉钉/企业微信/微信/小毅/Telegram/Discord/  │
│     │                Slack/WhatsApp                                │
│     ├─ protocol/      ACP（Agent Connect Protocol）                │
│     ├─ tui/           TUI 连接                                     │
│     └─ web/           WebSocket + Web 前端（Vite 产物）            │
│   gateway/routing / message_handler / im_pipeline / hooks /        │
│   heartbeat / cron                                                    │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Server / Runtime / Harness 层                       │
│   server/runtime/agent_adapter/      (deep / code / interface)      │
│   server/runtime/skill/              (skill_manager + skilldev)     │
│   server/runtime/session / agent_manager / agent_warm_pool /         │
│   tenant_agent_pool / proactive_adapter / a2ui / debug_trace        │
│   agents/harness/team/              (Team Manager + 分布式)         │
│   agents/harness/code / claw / work / common                        │
│   agents/swarm/                      (声明式 swarm 装配)             │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            业务 / Skill 层                            │
│   resources/agent/workspace/skills/                                  │
│     ├─ finance-report/        ★ 本次重点解读：完整金融分析 skill      │
│     ├─ project-maintainer/    项目维护 agent                          │
│     ├─ skill-omni-creation/   多模态 skill 生成                       │
│     ├─ swarmskill-creator/    swarm skill 创建                       │
│     ├─ llm-wiki / ppt-creation / akg-agents …                       │
│     └─ + 14 个其它 skill                                              │
└─────────────────────────────────────────────────────────────────────┘
```

> **关键观察**：业务层 skill **完全在框架外**——它们就是普通 Python 包,通过 `SKILL.md` + `allowed_tools` 接入框架。这种"框架 vs 业务"的清晰切分,让本项目既能维护一个稳态核心,又能快速扩展到任意垂直领域。

### 1.2 重要的"非代码"资产

- `docs/zh/开发实践/` 5 篇：金融 Day1 评审、SKILL.md 编写规范、日报生成器、代码审查助手、本文档
- `代码评审报告.md` / `代码评审报告-Day3.md`：两轮高强度代码评审记录
- `example/赛题二提交样例/`、`example/上市公司列表.xlsx`：可执行示例 + 真实赛题输入
- `reports/finance-report/`：9 个标的 × 真实采集数据 + 13 篇方法论 KB + 1 篇示例研报(600519)
- `deploy/yuanrong / deploy/observability / docker/`：部署形态
- `packages/jiuwenswarm-tui/`：独立子包（终端 UI）
- `jiuwenbox/`：第二个独立子包（独立 CLI + 服务端 + Docker）

---

## 2. 核心框架深度解读

### 2.1 设计哲学：三个不动摇

从代码与文档可以提取三条铁律：

1. **声明式 swarm 装配 > 命令式 wire-up**  
   所有 rail / tool / subagent 都是 `Spec(params=...)`,通过 openjiuwen 框架的工厂解析。`jiuwenswarm/agents/swarm/DESIGN.md` 第 14–18 行把这一点写成了三条原则："纯声明式装配"、"成员共享 config 源"、"跨序列化边界靠 seed 重建"。

2. **Skill 是框架公民,不是脚本外挂**  
   框架强制 SKILL.md frontmatter (`name` / `version` / `description` / `allowed_tools`),所有 skill 都通过 `skill_manager.py` 注册与加载（参见 `SKILL.md 编写规范`）。`allowed_tools` 是权限闸门——这是"工具权限与安全"的底层落实。

3. **会话资源有主动治理**  
   Session-scoped adapter 由 `_evict_idle_session_adapters()` 管理（TTL 2 小时、每批最多清理 3 个、锁保护、活跃检测）,在 11 处关键路径（interrupt / 流结束 / 清理）后自动触发——长跑 IM 接入不会无限累积会话内存。测试覆盖密度（12.2 万行测试）为质量托底。

### 2.2 架构核心：Agent Harness + Swarm

#### 2.2.1 抽象层次

```
TeamAgentSpec                         (openjiuwen schema,跨进程可序列化)
 ├─ agents["leader"]   DeepAgentSpec
 └─ agents["teammate"] DeepAgentSpec
        ├─ rails    : list[RailSpec]            ← 拦截/改写 prompt 的横切组件
        ├─ tools    : list[BuiltinToolSpec]     ← bash/read_file/write_file/mcp
        └─ subagents: list[SubAgentSpec]        ← explore/plan/browser 子 Agent
            ↓
        DeepAgent（运行时载体）
```

**Rail（横切关注点）** 是这个框架最具特色的设计——任何想介入 prompt 生命周期的行为（响应规整、流式事件、人格化、人类参与 HITL、token budget 控制、evolution、skill 创建…）都注册为 rail,而非 monkey-patch Agent 内部。`jiuwenswarm/agents/swarm/providers/` 下按域分组声明了所有 rail：

- `member_rails.py`：`runtime_prompt` / `team_workspace_report_path` / `context_processor` / `plugin_rails`
- `evolution_rails.py`：`team_skill_evolution` / `team_skill_create` / `member_skill_evolution`
- `code_rails.py`：10 个 `swarm.code_*`（其中 lsp / confirm_interrupt / worktree 已下沉到 openjiuwen）
- `builtin_rails.py`：3 个 swarm 自有无参类 rail (`response_prompt` / `stream_event` / `avatar_prompt`)

**Tool（垂直能力）** 通过 `allowed_tools` 清单授权,具体由 `tools.py` / `runtime_tools.py` / `skills.py` 提供工厂：

- `base_tools` / `code_extra_tools`：基础工具集
- `cron_tools` / `send_file`：运行时工具
- `member_skill_toolkit`：成员级 skill 工具箱（决定哪个 skill 暴露给当前 agent）

**SubAgent（递归）** 让一个 Agent 把"探索/计划/浏览器"封装为子 agent,实现 `agent.subagent()` 这种递归调用,避免污染主 agent 上下文。

#### 2.2.2 装配入口：`assembly.py`

```text
请求 (mode, role, channel, session, project_dir, config.yaml)
        │
        ▼
 enrich_team_spec_for_swarm()    ← assembly.py
        │  ① register_swarm_providers()
        │  ② 建 SwarmBuildContext
        │  ③ 改写成员 spec（config_specs 折叠 + 属性烘焙进 params）
        │  ④ 挂 build_context + seed
        ▼
 TeamAgentSpec
        │
        ▼  spec.build(context=SwarmBuildContext)
 swarm provider 工厂  build_xxx(params, ctx)
        │  inp = XxxInput.resolve(params, ctx)
        ▼
 运行对象（Rail / Tool / SubAgentConfig）
```

**`SwarmBuildContext`**（`context.py`）是构造期的环境句柄：装载 `session_id` / `request_id` / `channel` / `project_dir` / `team_id` / `mode` / `language` / `role` 等 per-request / per-session / per-member 运行时句柄。它通过 `to_seed()` / `from_seed()` 完成序列化——这一设计让 **成员 spawn / 分布式部署 / 热恢复** 都能跨进程重建。

#### 2.2.3 注册表：纯字典 + `@harness_element`

```python
_RAIL_PROVIDER_REGISTRY:      dict[str, Callable]
_TOOL_PROVIDER_REGISTRY:      dict[str, Callable]
_SUBAGENT_PROVIDER_REGISTRY:  dict[str, Callable]
```

每个元素都标注 `ElementKind` 与 `ConstructionInput` 子类,**逐字段标注来源**（`PARAMS` = config 烘焙的属性,`CONTEXT` = 运行时环境）。`resolve()` 在构造期从 `params + context` 提取并 pydantic 校验。

这种"manifest + catalog + ConstructionInput"机制是 **横跨 openjiuwen 框架的契约**,本项目已将其下沉到框架,自身只保留 provider 声明与 `registry.py` 注册编排。

### 2.3 Team Manager：分布式运行期

`jiuwenswarm/agents/harness/team/team_manager.py`（2616 行）是 Team 生命周期管理中枢。核心职责：

- **Team 装配**：从 `load_team_spec_dict(config)` 解析 → 转 `TeamAgentSpec` → 调用 `enrich_team_spec_for_swarm`
- **分布式支持**：`distributed_runtime.py` 处理 PostgreSQL 协调、`is_pg_available` / `try_start_pg_cluster` / `fallback_distributed_to_local` —— **有 PG 时跑分布式,无 PG 自动回落到本地**（这种优雅降级是工程成熟度的体现）
- **A2X 远程成员引导**：`remote_member_bootstrap.py`（3019 行）处理 leader / teammate 跨进程握手,`release_a2x_reservations_for_session` 在会话结束回收
- **成员缓存**：通过 `kv_cache_hooks.py` + `team_skill_links.py` 复用配置与 skill 软链
- **监控钩子**：`TeamMonitorHandler`（在 handlers/）把每次团队事件落入 trace

### 2.4 Server Runtime：AgentServer

`server/runtime/agent_adapter/` 下三个大文件是 AgentServer 的核心,总行数 ≈ 19000 行：

| 文件 | 行数 | 职责 |
|---|---|---|
| `interface_deep.py` | 11817 | Deep Agent 适配器(最复杂的路径)|
| `interface.py` | 3348 | 通用 Agent 接口 |
| `interface_code.py` | (中等) | Code mode 适配器 |
| `team_helpers.py` | 3272 | Team 模式下辅助 |
| `agent_adapters.py` / `user_turn.py` | (较小) | 适配器注册 / 用户回合处理 |

其它 `runtime/` 子模块也值得关注：

- `agent_manager.py`：Agent 实例的池化与复用（`agent_warm_pool.py` 是冷启动优化,v0.2.4.beta3 的核心改动）
- `skill/skill_manager.py`（4419 行）：skill 注册、SKILL.md 解析、allowed_tools 闸门
- `session/`：会话元数据 + 工程 git 操作（`project_git.py` 2199 行）
- `tenant_agent_pool.py`：多租户隔离
- `proactive_adapter.py`：主动触发（heartbeat / cron / 调度）

### 2.5 Gateway：Channel Manager

`gateway/channel_manager/` 是把 IM / Web / TUI / ACP 各种入口归一为消息流的中枢,关键文件 `app_web_handlers.py` 6455 行,`message_handler.py` 4523 行,`feishu_connect.py` 3622 行（飞书是头号国内集成,代码量也最大）。

`im_platforms/` 下 9 个平台子目录,**每个平台一个 Connect 实现**——这是"水平可扩展"的典型表现。

### 2.6 其它重要子系统

| 子系统 | 路径 | 价值 |
|---|---|---|
| **Symphony（编排）** | `jiuwenswarm/symphony/` | 含 agent / models / retrieval / indexing / skill_retrieval / shared —— 一站式编排内核 |
| **A2UI（界面协议）** | `server/runtime/a2ui/` | 让 Agent 通过结构化指令驱动 UI 渲染 |
| **ACP（Agent Connect Protocol）** | `acp/` + `channels/acp/` + `gateway/channel_manager/protocol/acp/` + `extensions/sdk/` | Agent 与 Agent 跨进程通信协议 |
| **A2A（Agent-to-Agent）** | 由 `a2a-sdk[http-server]==1.0.0` 提供 | 标准化跨供应商的 Agent 互通 |
| **E2A（Environment-to-Agent）** | `common/e2a/` | 环境 → Agent 通知（事件流） |
| **Common** | `common/` (config 2594 行 + utils 2137 行) | 全局配置 + 工具函数 |
| **Security** | `common/security/` | 工具权限 + 敏感拦截 |

---

## 3. 业务 Skill 深度解读：finance-report

> 这是本项目最具交付价值的部分,9 个 commit / 3 天完成一个可投产的金融研报 Agent,在 AFAC 赛题四（六板块 + Portfolio.json + 个股研报）跑出 100 分。

### 3.1 顶层架构：5 个子 Agent + 编排 + 反馈环

```
            ┌─────────────────────────────────────┐
            │   ReportOrchestrator（编排器）       │
            │   jiuwenswarm/.../orchestrator.py   │
            └─────────────────────────────────────┘
                          │
   ┌──────────────┬───────┴────────┬───────────────┬──────────────┐
   ▼              ▼                ▼               ▼              ▼
PlannerAgent  ResearcherAgent  WriterAgent    ReviewerAgent  InvestorAgent
(任务规划)    (数据研究)        (报告撰写)    (审查校验)     (选股/仓位)
                          │
                          ▼
              反馈回路（≤ 2 轮重写）
```

| Agent | 文件 | 核心职责 | 关键设计 |
|---|---|---|---|
| Planner | `agents/planner.py` | 判断任务类型,拆解采集与分析子任务 | 链式推理起点 |
| Researcher | `agents/researcher.py` | 迭代式 Deep Research 采集 + RAG 检索 | 混合记忆分流,大表格"头尾 5 行"压缩 |
| Writer | `agents/writer.py` | 分治式生成：先 YAML 大纲 → 逐段撰写 → 图片本地化 | 分治避免 LLM 长上下文失忆 |
| Reviewer | `agents/reviewer.py` | 溯源/图文/结构/合规校验,不通过回流 | 引用率 ≥ 90% |
| Investor | `agents/investor.py` | 公司池评分 + 仓位配置（满仓/半仓/空仓） | 单标的权重 ≤ 0.4,权重之和 ≤ 1.0 |

### 3.2 数据采集层（collectors/）

| 模块 | 文件 | 关键能力 |
|---|---|---|
| 公司池加载 | `pool_loader.py` | 读 `example/上市公司列表.xlsx`,白名单校验 |
| 行情采集 | `quote_collector.py` | **三级降级链**：akshare(东方财富) → 腾讯(前复权) → 新浪(不复权) |
| 新闻采集 | `news_collector.py` | **三级降级链**：搜狗新闻 → 新浪滚动财经(关键词过滤) → Bing(代理) + **迭代式 Deep Research** (`max_depth=3`,信息饱和或达上限终止) |
| 财报采集 | `filing_collector.py` | akshare 财务摘要（最近 8 个报告期 + 近期公告）|
| 财务 RAG | `rag_retriever.py` | 13 篇方法论文档 → 智谱 embedding-3（2048 维）→ 离线兜底为字符 bigram TF-IDF |

**降级链是这层的灵魂**——任何一个外部数据源挂掉都不会导致整体失败,且 `source` 字段记录"实际命中",满足"成果可复现"硬约束。

### 3.3 知识库 + 记忆

- **方法论 KB（外部记忆）**：`reports/finance-report/finance_kb/docs/` 13 篇种子文档（白酒行业/银行业/半导体/新能源/估值方法/杜邦分析/偿债能力…）。`rag_retriever.py` 用"混合分块 + 混合检索 + 重排"三件套：

  ```python
  # 混合分块：按标题层级切块,保留标题路径上下文
  # 混合检索：向量相似度 + BM25 双路召回
  # 重排：RRF 融合 + query 词命中加权 → top_k 注入上下文
  ```

  embedding 双层策略：**主路径智谱 embedding-3（2048 维）,失败兜底为本地字符 bigram TF-IDF**（零依赖、确定性、可离线复现）。

- **混合记忆（`common/hybrid_memory.py`）**：分流策略防止批量分析时记忆爆炸——
  - **大表格**只入短期,压缩为"表头 + 前后 5 行"
  - **分析结论**同步沉淀 `memory/long_term/`,跨标的分析以摘要形态注入后续上下文
  - **方法论**经 RAG 外部记忆按需检索（不污染工作记忆）

### 3.4 分析引擎（analyzers/）

| 模块 | 行数(估) | 职责 |
|---|---|---|
| `code_executor.py` | ~400 | **Notebook 式 + AST 白名单沙箱**：持久化有状态、跨代码块保留变量、stdout/stderr 捕获、新变量追踪 |
| `finance_analyzer.py` | ~600 | 财务分析（盈利/偿债/成长/估值）,口径取披露值 |
| `industry_analyzer.py` | ~500 | 行业分析（板块内两两对比）|
| `macro_analyzer.py` | ~400 | 宏观分析（GDP/CPI/PMI/政策）|

**`CodeExecutor` 是整个 skill 的"代码闸门"**,AST 白名单 + 禁内置函数（`exec` / `eval` / `compile` / `open` / `getattr` / `setattr` / `dir`…）+ 禁 dunder 属性 + 禁 IPython 系统命令 API——这是"框架约束,不得绕过"在 skill 层的体现。

### 3.5 报告生成（generators/）

- `chart_generator.py`：matplotlib + 中文字体 SimHei,Agg 后端预配置
- `report_writer.py`：**分治式生成**（先 YAML 大纲 → 逐段撰写 → 图片本地化）
- `citation_checker.py`：逐条事实溯源校验（**引用率 ≥ 90%** 硬指标）

### 3.6 Day 1–3 演进时间线

```
Day 1 (370dc69)  ─── 评审问题修复：技能目录入库 / 依赖声明 / 单元测试 / 采集时间戳
                          │
                          ▼
Day 2 (6815297)  ─── Deep Research 新闻采集 / RAG 知识库 / 混合记忆 / CodeExecutor / 财务分析器
                          │
                          ▼
Day 3 (348c0a2)  ─── 五分析 / 生成引擎实装 + 分治式研报打通（600519 审查 100 分）
                          │
                          ▼
评审修复 Day 1/2 (b312eb1) ─── 14 项问题全量修复（沙箱绕过/成交量单位/假同比/索引原子性…）
                          │
                          ▼
评审修复 Day 3 (3739081) ─── 三项高危（C1 宏观错值 / H1 降级引用率 / H2 PE 编造）
                          │
                          ▼
评审收尾 (27694f2)   ─── 剩余问题（M1–M4 / L1–L3）+ 11 个回归用例
```

每次提交都是"先实装 → 再被评审 → 再修一轮",**测试先行的回归保护链**让"修高危问题"不会引入新问题。

### 3.7 关键创新与痛点修复（来自两次评审报告）

| 等级 | 问题 | 修复 |
|---|---|---|
| 🔴 C1 | AST 沙箱可被 `getattr(obj, "__class__")` 字符串式绕过 → 实测执行了系统命令 | 扩充 BLOCKED_NAMES + 拦 dunder 字符串常量 + 建议生产叠加进程级隔离 |
| 🟠 H1 | 行情源成交量单位"手/股"混用（akshare 手 vs 腾讯/新浪 股）| 统一为股 + 注释写明单位 |
| 🟠 H2 | 同比增速在缺上年同期时"回退上一期"→ 跨季失真的假同比 | 缺失不计算 + 单列 `qoq_growth` |
| 🟠 H3 | RAG 索引写入非原子、加载无损坏恢复 → 一次中断永久崩溃 | `os.replace()` 原子替换 + 损坏自动重建 |
| 🟠 H1(Day3) | 降级引用率不足 → 引用质量未达标 | RAG + 引用率闸门升级 |
| 🟠 H2(Day3) | PE 编造（年化方式不对）| 固定为披露口径或明示估算方式 |
| 🔴 C1(Day3) | 宏观指标错值 | 全部用披露值,禁止自行推算 |

---

## 4. 测试、CI、文档

### 4.1 测试结构

```
tests/                                # 顶层集成 / E2E
├── agents/swarm/                     # swarm 装配测试
├── auth/                             # 认证测试
├── integration/                      # 集成测试
├── symphony/                         # symphony 编排测试
├── system_tests/                     # 系统级
├── ui_e2e/                           # UI 端到端（SKILL.md 在这里）
├── unit/                             # 单元测试（含 deep_agent / channel）
└── unit_tests/                       # 单元测试（含 a2ui/acp/agents/auto_harness/
    ├── agents/                       # channel/cli/common/e2a/evolution/extensions/
    ├── agentserver/                  # finance/gateway/server/symphony）
    ├── channels/
    ├── cli/
    ├── common/
    ├── e2a/
    ├── evolution/
    ├── extensions/
    ├── finance/        ← ★ 8 个文件,全部 mock 无网络
    ├── gateway/
    ├── server/
    └── symphony/
```

> **观察**：测试按"系统横切(顶层)+ 单元(按子目录)"二分,这种命名重复（`unit` 和 `unit_tests`）看着冗余,实际上是新旧两套测试基础设施并存——重构成单一目录会是后续清理项。

`tests/unit_tests/finance/` 15 个测试文件（174 个测试函数）覆盖整个金融 skill,且全部 mock 无网络依赖,符合"零外部依赖"硬约束。

`pytest.ini` 启用严格警告(`filterwarnings = error`),意味着任何 DeprecationWarning 都会让 CI 失败——这是高质量工程的标志。

### 4.2 CI 与工具链

- `pyproject.toml` 单一构建配置
- `uv.lock` 锁定依赖（uv 是 Astral 的新一代包管理）
- `dependency-groups`:test / lint / dev 三组（与 `optional-dependencies.test` 保持同步）
- `lint` 组：ruff + pylint + mypy + codespell ——**四道静态检查**
- `[tool.setuptools.package-data]` 精心配置前端 dist / resources / symphony yaml / extensions yaml 的数据打包

### 4.3 文档体系

```
docs/zh/开发实践/                # 中文开发实践（5 篇 + 本篇）
docs/zh/assets/                  # 图片/视频
docs/en/                         # 英文版（参考文档,如 InstallGuide / Quickstart / Modes / Channels…）
docs/README.md / README_EN.md    # 总索引
README.md / README_CN.md         # 项目根 README
TESTING.md                       # 测试手册
```

中英文档基本一一对应,这是国际化项目的标志。

---

## 5. 关键创新点总结

| 创新 | 体现 |
|---|---|
| **声明式 swarm 装配** | RailSpec / ToolSpec / SubAgentSpec + ConstructionInput 反射 |
| **Rail 横切关注点** | 任何想介入 prompt 的行为都是 rail,而非 monkey-patch |
| **Skill 框架公民** | SKILL.md frontmatter + allowed_tools 权限闸门 + 22 个内置 skill |
| **种子跨进程恢复** | SwarmBuildContext.to_seed/from_seed + build_context_factory |
| **优雅降级** | 数据源三级降级、embedding 双层策略、PG 不可用回落本地 |
| **混合 RAG（混合检索 + 重排）** | 向量相似度 + BM25 + RRF 融合 |
| **混合记忆分流** | 大表格短期压缩 / 结论长期沉淀 / 方法论外部 RAG |
| **分治式报告生成** | YAML 大纲 → 逐段 → 图文同源 |
| **可复现性硬约束** | source / collected_at / search_trace 全部记录,引用率 ≥ 90% |
| **AST 沙箱 + 测试先行** | CodeExecutor 白名单 + 11 个回归用例保护 |

---

## 6. 工程亮点与改进空间

### 6.1 亮点

1. **测试规模 12.2 万行,金融 skill 174 个测试全 mock 无网络**——质量托底扎实
2. **会话资源主动治理**——`_evict_idle_session_adapters()` TTL 驱逐 + 11 处关键路径触发,长跑稳定性设计到位
3. **跨进程 swarm 部署成熟**（distributed_runtime + remote_member_bootstrap）
4. **频道覆盖广**（9 个 IM 平台 + Web + TUI + Desktop）
5. **Skill 即插即用**（22 个内置 skill 演示了完整扩展面）
6. **数据采集三级降级链 + 双层 embedding**——真实世界鲁棒性
7. **两次高强度代码评审 + 11 个回归用例**——质量闭环
8. **国际版 + 国内版文档对称**——国际化路径

### 6.2 改进空间（基于代码观察,非贬低）

1. **`tests/unit/` 与 `tests/unit_tests/` 命名重复**——历史包袱,可清理
2. **核心接口文件超过 1.1 万行**（`interface_deep.py`）——可拆分为多个职责单一的子文件
3. **CVE 防御式依赖钉版本**（python-multipart / lxml / pillow）——反映对供应链的重视,但也说明依赖图复杂
4. **HarmonyOS 兼容分组**（`optional-dependencies.harmony`）——可考虑抽象为独立安装脚本
5. **`code_executor.py` AST 白名单在评审报告 C1 中已被证伪**——生产环境必须叠加进程级隔离

---

## 7. 与同类项目的对比视角

| 维度 | JiuwenSwarm | LangGraph | AutoGen | CrewAI |
|---|---|---|---|---|
| 多 Agent 范式 | Leader-Team | Graph | GroupChat | Role-based |
| 跨进程分布式 | ✅ 成熟 | 弱 | 弱 | 弱 |
| Skill 自进化 | ✅ 框架级 | ❌ | ❌ | ❌ |
| IM 频道广度 | ✅ 9 个 | ❌ | ❌ | ❌ |
| Skill 市场 / Hub | ✅ Swarm Skills Hub | ❌ | ❌ | ❌ |
| 开箱即用业务 | ✅ finance-report 100 分 | 通用 | 通用 | 通用 |
| 测试密度 | ✅ 极高（12.2 万行）| 中 | 中 | 低 |

JiuwenSwarm 的差异化定位：**"框架 + 真实业务 skill + IM 多端 + 分布式"一体化**——这恰恰是企业级落地最缺的拼图。

---

## 8. 进一步阅读路线

按从浅到深：

1. **5 分钟理解**：`README.md` → `docs/zh/开发实践/金融分析报告生成Agent-Day1评审.md`（看历史背景）
2. **30 分钟理解**：`docs/en/Quickstart.md` + 本文档 + 跑一次 `python run_report.py company --target 600519`
3. **2 小时深入**：`jiuwenswarm/agents/swarm/DESIGN.md`（swarm 设计）+ `team_manager.py` 前 100 行 + `orchestrator.py` 全文
4. **1 天实操**：在 `jiuwenswarm/resources/agent/workspace/skills/` 下新建一个 skill,参照 `SKILL.md 编写规范`,在 `tests/unit_tests/` 下加测试

---

## 9. 附录：核心文件清单（架构必读）

| 优先级 | 文件 | 行数 | 必读原因 |
|---|---|---|---|
| ⭐⭐⭐ | `jiuwenswarm/agents/swarm/DESIGN.md` | 150+ | 整个 swarm 装配的设计哲学 |
| ⭐⭐⭐ | `jiuwenswarm/agents/swarm/assembly.py` | ~400 | 装配入口 |
| ⭐⭐⭐ | `jiuwenswarm/agents/swarm/context.py` | ~200 | SwarmBuildContext 的秘密 |
| ⭐⭐⭐ | `jiuwenswarm/agents/harness/team/team_manager.py` | 2616 | Team 生命周期与分布式 |
| ⭐⭐⭐ | `jiuwenswarm/resources/agent/workspace/skills/finance-report/orchestrator.py` | ~150 | 业务编排 5 Agent 闭环 |
| ⭐⭐⭐ | `jiuwenswarm/resources/agent/workspace/skills/finance-report/SKILL.md` | ~120 | 框架与业务 skill 的契约 |
| ⭐⭐ | `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py` | 11817 | 复杂适配器,值得分块读 |
| ⭐⭐ | `jiuwenswarm/server/runtime/skill/skill_manager.py` | 4419 | Skill 注册与解析 |
| ⭐⭐ | `jiuwenswarm/agents/harness/team/remote_member_bootstrap.py` | 3019 | 分布式成员引导 |
| ⭐⭐ | `jiuwenswarm/resources/agent/workspace/skills/finance-report/analyzers/code_executor.py` | ~400 | AST 沙箱的细节与局限 |
| ⭐ | `pyproject.toml` | 207 | 依赖图与构建配置 |
| ⭐ | `代码评审报告.md` / `代码评审报告-Day3.md` | 14k + 10k | 两次评审全景 |
| ⭐ | `jiuwenswarm/agents/swarm/providers/` | 多文件 | 13 个 rail + tool 的工厂声明 |
| ⭐ | `docs/zh/开发实践/JiuwenSwarm-SKILL.md编写规范.md` | ~120 | 写新 skill 的模板 |

---

> 本文档配合 `代码评审报告.md`、`代码评审报告-Day3.md` 与 `jiuwenswarm/agents/swarm/DESIGN.md` 阅读效果最佳。