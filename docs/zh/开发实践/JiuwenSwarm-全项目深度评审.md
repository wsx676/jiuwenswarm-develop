# JiuwenSwarm 全项目深度评审（架构 / 测试 / 性能）

> 评审日期：2026-08-18（v2,经复核修订）
> 评审范围：main HEAD `27694f2` 之后新增的代码、核心接口层（`interface_deep.py` / `skill_manager.py` / `team_helpers.py`）、finance-report skill 全量已读、3 个代表性 skill 抽样审计
> 评审维度：架构与可维护性 / 测试与质量 / 性能与资源
> 前置参考：`代码评审报告.md`（Day 1/2）、`代码评审报告-Day3.md`（Day 3），两份均已合并入 HEAD；本评审仅覆盖**未在前两份报告中的新增问题**
> **v2 修订说明**：初版评审（同日早间）中 H3/M4/M6 及部分性能表断言经源码复核被证伪或需弱化,已在本版修正；文末附「复核修订记录」。核心代码行数初版误计为 8 万（xargs 分批截断）,实际 28.9 万。
> 分级标准：C（阻断）/ H（应尽快修复）/ M（建议修复）/ L（可选优化）

---

## 1. 总体评价

JiuwenSwarm 框架在工程化层面达到了**企业级开源 Agent 框架**应有的成熟度：12.2 万行测试托底、严格警告 pytest、22 个内置 skill 演示面广、声明式 swarm 装配 + 优雅降级 + 跨进程部署设计成熟、会话资源有 TTL 主动治理（复核确认）。主要短板是**核心接口文件过大**（`interface_deep.py` 11817 行 / 293 个方法）、**git 子进程缺乏超时保护**（复核确认）、**宽异常捕获项目级蔓延**（328 个文件含 `except Exception`,其中 298 个后接 pass 或静默）、**集成/性能测试占比偏低**。这些问题不阻断功能,但会在大流量、长运行、跨进程场景下逐步暴露。本次评审输出 13 项可验证问题（C 0 / H 3 / M 5 / L 5）,覆盖三个维度。

---

## 2. 问题清单

### HIGH（应尽快修复）

#### H1. `_git_clone` / `_git_pull` / `_git_get_commit` 无 timeout，长跑卡死可阻塞 asyncio 任务数小时

- **位置**：`jiuwenswarm/server/runtime/skill/skill_manager.py` L3833-3894
- **问题**：三个方法都通过 `asyncio.create_subprocess_exec("git", ...)` 但未传 `timeout`，GitHub 大仓库深历史（>1GB）或网络抖动时，subprocess 会无限挂起。`SkillManager._sync_marketplace_repos` 串行遍历所有 marketplaces，一个挂死会阻塞整个 install/install_status 流程；同时由于 `_SKILLNET_INSTALL_JOBS` 是模块级 dict（L57），挂死的 job 会一直占据内存直到进程退出。
- **可复现**：在 `~/.jiuwenswarm/config/marketplaces.yaml` 中配一个 unreachable host（如 `git@github.com:nonexistent/repo.git`），触发 `skills.install`，观察 asyncio task 永远不结束。
- **修复**：统一加 `timeout=30.0`（或环境变量 `_GIT_TIMEOUT`），`asyncio.wait_for(proc.communicate(), timeout)` + `TimeoutError` 处理返回 None，记录 warn 日志。`_sync_marketplace_repos` 应改为 `asyncio.gather(*tasks, return_exceptions=True)` 并发，失败独立处理。

#### H2. `_run_git` 在 `interface_deep.py` 内捕获所有 `Exception` 然后 `pass`，掩盖了 cwd 不存在等真实错误

- **位置**：`jiuwenswarm/server/runtime/agent_adapter/interface_deep.py` L2171-2186
- **问题**：`except Exception: pass` 让 git 调用失败时静默，fallback 到 `git_branch = "N/A"`。当 `project_dir` 是临时目录被删、或 cwd 越权、或 `_resolve_stable_git_facts` 抛出非 git 错误（如编码错误）时，真实错误信号被吞掉，运营定位"为什么 runtime_state 字段是 N/A"需要重读代码。这是项目级风格问题的缩影：全项目 328 个文件含 `except Exception`,其中 298 个后接静默处理。
- **可复现**：把 `project_dir` 指向一个 `/proc/self/...` 之类不可读的路径，调用 `_write_runtime_state`，观察 yaml 里 `git_branch: N/A` 没有任何错误日志。
- **修复**：区分 `FileNotFoundError`/`PermissionError`（环境问题，可静默）与 `subprocess.SubprocessError`/`UnicodeDecodeError`（代码 bug，必须 `logger.warning`）；对捕获的异常类型收紧；**项目级规范**禁止 `except Exception: pass`，至少 `logger.debug(..., exc_info=True)`。

#### H3. `subprocess.run(git, ...)` 5 秒 timeout 无 TimeoutExpired 单独处理，失败静默返回空串

- **位置**：`jiuwenswarm/server/runtime/agent_adapter/interface_deep.py` L2162-2168
- **问题**：`_run_git` 内层 5 秒 timeout，在 CI 或大仓库下会频繁 timeout，且 `subprocess.TimeoutExpired` 未被单独 catch（只被 `_resolve_stable_git_facts` 外层的宽 `Exception` 吞掉）。失败时 `result.stdout.strip() if returncode == 0 else ""` 静默返回空串，导致 `git_status` 等字段恒为空，与"git 未启用"无法区分。
- **修复**：把 timeout 提到配置（默认 5s 可调），TimeoutExpired 单独 `logger.warning`；空返回时记录 "git timeout / disabled / not-repo" 三态以助排障。

> **复核删除项**：初版 H3（session adapter 无 LRU 上限会 OOM）经实读源码**证伪**——`interface_deep.py` L1442-1460 存在 `_evict_idle_session_adapters()`：TTL 2 小时（`SESSION_ADAPTER_IDLE_TTL_SEC = 2*60*60`，L1078）、每批最多清 3 个（L1079）、锁保护 + 活跃检测 + waiter 检查，且在 11 处关键路径（L7139/7146/7511/7566/7684/8354/8625/8948/10710/10795 等）的 finally 块中自动触发。该机制设计完备，不构成问题。初版 M6（`_session_adapter_last_used` 无消费者）同被证伪——L1451 即其消费者。

---

### MEDIUM（建议修复）

#### M1. `interface_deep.py` 单文件 11817 行 / 293 个方法，严重违反 SRP，模块边界不可读

- **位置**：`jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`（整个文件）
- **问题**：11817 行 = 1 个 `JiuWenSwarmDeepAdapter` 类（经 awk 精确统计 **293 个方法**）+ 顶部 60+ 个模块级 helper。任何改动都需要全文件编译（IDE 索引慢），review 时改动 diff 跨越数千行。
- **修复**：按职责拆 6-8 个子模块，如 `interface_deep/_runtime_state.py`（L2150-2210 周边）、`interface_deep/_browser.py`（L2212-2400 周边）、`interface_deep/_heartbeat.py`、`interface_deep/_skill_evolution.py`、`interface_deep/_subagent.py`，主类保留编排逻辑。

#### M2. `SkillManager._SKILLNET_INSTALL_JOBS` 模块全局 dict 跨实例共享，无大小上限 + 失败后无清理

- **位置**：`jiuwenswarm/server/runtime/skill/skill_manager.py` L57
- **问题**：模块全局可变 dict，记录所有 install job 状态，无 max-size，失败/异常的 job 永远不会从 dict 移除。`SkillNetInstallError` 路径下可能累积数百条 dead record；同时该设计隐含 **"必须用模块全局而非实例属性"** 的语义，但代码注释（L54-56）只说了原因，没说边界。
- **修复**：加 `_MAX_INSTALL_JOBS = 1000` LRU 弹出；失败/取消 job 立即 `del _SKILLNET_INSTALL_JOBS[job_id]`；同时把"为什么用模块全局"加进 docstring 并单元测试。

#### M3. `_safe_rmtree` 内含三重 `try/except` + `pass`，失败路径不可观测

- **位置**：`jiuwenswarm/server/runtime/skill/skill_manager.py` L300-359
- **问题**：函数体里 6 处 `except ...: pass`，失败原因完全丢失。当 Windows 上 git 锁文件导致 `shutil.rmtree` 持续失败时，运维无法判断是权限问题、文件占用还是路径错误；最终 `logger.warning("删除目录失败（已重试 %d 次）: %s", max_retries, path)` 也只给路径，不给具体文件。
- **修复**：Windows 路径上收集"被占用文件清单"，返回 `dict[str, str]` 含 `{failed_path: error}`；非 Windows 路径简化掉 os.walk 部分。

#### M4. `team_helpers.py` `_truncate_team_tool_result_event` 截断头部保留、丢弃尾部,异常堆栈若在后半段会丢失

- **位置**：`jiuwenswarm/server/runtime/agent_adapter/team_helpers.py` L1303-1324
- **问题**（经实读复核,初版描述"未保留关键诊断信号"**部分证伪**——截断后**确有** `truncated: True` 与 `original_size` 标记,这部分做得对）。剩余问题：截断策略是 `value[:LIMIT]` 只保留头部,若工具输出前半段是长列表/长正文、异常堆栈在后半段（如 pytest 长输出末尾的 `FAILED`/`Traceback`）,根因信息被切掉。
- **修复**：改为头尾各保留一半（如前 70% + `...[truncated N chars]...` + 后 30%）,或在截断前检索 `Traceback`/`Error` 关键段优先保留。

> **复核删除项**：初版 M6（`_session_adapter_last_used` 无消费者）已并入 H3 复核说明——L1451 即消费者,不构成问题。

#### M5. finance-report Day3 评审问题修复已确认落地,但缺端到端 Reviewer 断言

- **位置**：`tests/unit_tests/finance/`（15 个文件 / 174 个测试函数）
- **问题**（经 `git show 27694f2` 复核,初版"需查"**已确认**）：
  - ✅ M2 已修：`min_position_count` 不足时 decision_notes 留痕阐明（软约束）+ `decision_log/decision.json` 落盘可追溯
  - ✅ 宏观 mock 已升级为**多期乱序 DataFrame** 并断言取最新期（`test_macro_analyzer.py` L13-15,末行非最新期的乱序设计复现 C1）
  - ✅ M1/M3/M4/L1/L2/L3 全部修复且带 11 个回归用例（170→181 passed）
  - ❌ 仍缺：Day3 报告 H1 指出的"降级全报告必须通过 Reviewer"端到端断言,现 174 个测试仍全部为 mock 单元测试
- **修复**：补一条端到端用例：降级生成的完整 draft 经 `ReviewerAgent().review` 必须 `passed`。

---

### LOW（可选优化）

#### L1. `tests/unit/` 与 `tests/unit_tests/` 命名重复，历史包袱

- **位置**：`tests/unit/`（7 个文件）与 `tests/unit_tests/`（286 个文件）并存
- **问题**：新代码一律走 `unit_tests/`，`unit/` 残留旧测试。两个目录的 `conftest.py` 可能存在 fixture 重复定义或冲突。增加了新人理解成本。
- **修复**：批量迁移 `unit/*.py` 到 `unit_tests/` 对应子目录，删除 `unit/` 目录；在迁移前确认所有 fixture 已被 `unit_tests/` 覆盖。

#### L2. 多个 `except Exception: pass` 与 `except Exception:` + 仅 debug log 的宽捕获

- **位置**：全项目 328 个文件含 `except Exception`,其中约 298 个文件后接 pass 或静默处理（grep 统计）;`interface_deep.py` 单文件 24 处、`team_helpers.py` 多处、`interface.py`、`interface_code.py`
- **问题**：与 H2 同源，但 L2 是项目级代码气味而非单点问题。`exc_info=True` 不写 / 不区分异常类型 / 不 `raise from`，导致根因丢失。
- **修复**：lint 规则禁止 `except Exception: pass`；CI 加 `pylint: W0703(bare-except)` + `W0707(use-different-raise)`；自定义 AST checker 拦 `except Exception` 后仅 `pass` 的模式。

#### L3. （已解决,记录留档）finance-report `min_positions` 配置读取后未使用

- **位置**：`jiuwenswarm/resources/agent/workspace/skills/finance-report/agents/investor.py`
- **复核结论**（`git show 27694f2` 确认）：Day3 M2 已修复——达标标的不足 `min_position_count` 时 decision_notes 留痕阐明（定位为软约束,不强制空仓）,`_save` 落盘 `decision_log/decision.json`（评分/权重/空仓理由/分散度提示可追溯）。**无需再动**。

#### L4. `skill-omni-creation` 的 `environment_gate.py` 在 Linux 上自动尝试 `sudo` 提权，触发企业防火墙告警

- **位置**：`jiuwenswarm/resources/agent/workspace/skills/skill-omni-creation/scripts/environment_gate.py`（估）
- **问题**：SKILL.md L41 写明 "Debian/Ubuntu 类 Linux 在具备 root 或免密 sudo 时自动补齐 Chromium 系统库"。在企业环境（IDS/IPS 监控 sudo 调用）或共享开发机上，自动 sudo 会被标记为可疑行为；同时失败时写入 `environment_status.json` 不抛错，反而让用户以为是 skill 自身失败而非环境问题。
- **修复**：首次 sudo 调用前向用户确认（交互模式）；非交互模式仅记日志，提示用户手动执行；失败状态显式说明"环境需要 sudo 但被禁用"。

---

## 3. 性能与资源维度补充发现

### 3.1 性能

| 关键路径 | 当前实现（v2 复核后） | 风险 |
|---|---|---|
| 冷启动 | `agent_warm_pool.py` + `_RuntimeState.workspace.get_node_path()` 节点路径解析 | v0.2.4.beta3 已优化，但**无公开 benchmark**；建议 `tests/perf/` 加冷启动时间断言 |
| Skill 在线检索 | `handle_skills_online_search` 内 `asyncio.gather(...)` 并发多源（L1122,复核确认） | ✅ 已并发,无风险 |
| Git marketplace 同步 | `_sync_marketplace_repos` **串行** `for` 遍历 marketplaces（L3896-3925,复核确认） | 10+ 个 marketplace 时总耗时线性叠加;且每步 git 无超时（见 H1）;改 `asyncio.gather` 并发 |
| RAG 索引 | `rag_retriever.py` `add_documents` 增量向量化（"只向量化新块,避免 O(N²) API 成本",L233-234 注释,复核确认） | ✅ 已有增量,无风险 |

### 3.2 资源

| 资源 | 当前（v2 复核后） | 风险 |
|---|---|---|
| 内存·会话 adapter | `_evict_idle_session_adapters()` TTL 2h + 批量驱逐（复核确认,初版"无 LRU 会 OOM"**证伪**） | ✅ 机制完备 |
| 内存·install jobs | `_SKILLNET_INSTALL_JOBS` 模块级 dict 无上限（M2） | 失败 job 永久残留,缓慢增长 |
| 任务数 | `_runtime_state_write_task` 单 task 替换旧 task 但不 await 旧 task（L1290-1309,复核确认仍在） | 旧 task 仍在飞行,可能 race;应在替换前 cancel/await 旧 task |
| 文件句柄 | `_safe_rmtree` 内 `os.walk` 嵌套删除异常路径 | 极端场景文件描述符可能泄漏（与 M3 关联） |
| 网络连接 | `_skillnet_network_context()` session 复用 | session 无连接池上限说明;并发 install 大量并发时行为未验证 |

---

## 4. 测试与质量维度补充发现

### 4.1 测试覆盖错位

- **集成测试比重过低**（复核修正数字）：`tests/integration/` 仅 **2** 个文件,而 `tests/unit_tests/` 286 个文件——比例 1:143。真实分布式 / 跨进程 / 真实数据库场景覆盖严重不足（初版误记 integration 为 ~10 个）。
- **性能 / 负载测试缺失**：`tests/` 下无 `perf/` / `load/` / `stress/` 目录，运行时性能无法 CI 守护。
- **finance-report 15 个测试文件 / 174 个测试函数全部 mock**（复核修正:初版误记 8 个文件）：覆盖了函数，但**没有"完整 draft 经 Reviewer 必须 passed"的端到端断言**（Day3 报告 H1 已指出,`27694f2` 修复清单中未见补齐）。

### 4.2 测试质量

- **pytest.ini 严格警告**（`filterwarnings = error`）—— 质量标志，但同时也意味着任何新依赖的 DeprecationWarning 都会让 CI 失败，易碎。
- **fixture 复用**：未审，但 `unit/` 与 `unit_tests/` 重复目录可能存在 fixture 重复定义。

### 4.3 代码气味指标（v2 复核修正,初版三处"干净"结论被证伪）

| 指标 | v2 实测 | v1 误记 | 备注 |
|---|---|---|---|
| 含 `except Exception` 的文件 | **328 个**（其中约 298 个文件后接 pass/静默） | 仅 interface_deep 24 处 | 项目级蔓延,见 H2/L2 |
| `TODO/FIXME` | **54 处**（多为 prompt 文案中的 `TODO_` 常量,真实代码 TODO 需人工分拣） | 0 | 初版 grep 漏检（未递归 resources/） |
| `shell=True` | **0 处实际调用**（grep 命中的 2 处是 `project_git.py` 中"禁止 shell=True"的注释） | 0 | ✅ 结论碰巧正确 |
| `subprocess.run` 无 timeout | 至少 4 处 | 至少 4 处 | ✅ 不变 |
| `eval()` | **1 处**:ascend-moe-optimizer-auto-trace `trace_utils.py` L42 `eval(expr, {"__builtins__": {}})` 解析 C 宏定义 | 0 | 受限 eval（空 builtins）,风险低但建议换 `ast.literal_eval` |

---

## 5. 架构维度补充发现

### 5.1 接口层大小

- `interface_deep.py` 11817 行 → M1
- `skill_manager.py` 4419 行 → 可拆为 `skill_manager.py`（主类）+ `_install.py`（install/install_builtin）+ `_git.py`（git 操作）+ `_marketplace.py`（marketplace sync）
- `team_helpers.py` 3272 行 → 可拆为按 event 类型划分子模块
- `interface_code.py` 1754 行 → 适中

### 5.2 模块边界

- `tests/unit/` vs `tests/unit_tests/` 重复 → L1
- `tests/ui_e2e/SKILL.md` 与业务 skill 同名约定不一致（命名是测试名还是真 skill？）
- `jiuwenswarm/symphony/` 与 `jiuwenswarm/agents/swarm/` 命名相似但职责不同（symphony 是编排内核，swarm 是装配声明），新人易混。

### 5.3 文档/代码一致性

- `docs/zh/开发实践/README.md` 现含 7 篇,与 `docs/en/development-practices/` 是否一一对应需查。
- `代码评审报告.md` 与 `代码评审报告-Day3.md` 与 HEAD 不再同步（评审的是旧 commit）,但 `27694f2` 提交信息已注明全部修复项——建议在两份报告头部加"修复已合并于 b312eb1/3739081/27694f2"标注。

---

## 6. 安全与权限维度（bonus）

未纳入评审范围，但发现的可疑项，值得后续专项：

| 项 | 位置 | 备注 |
|---|---|---|
| `_git_clone(url, dest)` url 无 host 白名单 | skill_manager.py L3833 | marketplace yaml 可配任意 git 仓库；若 yaml 被外部控制，可远程代码执行 |
| `_safe_child_path` 防 path traversal 但 git url 无校验 | 同上 | 与上一项配套 |
| skill-omni-creation 自动 sudo | environment_gate.py | L4 已记录 |
| `_ImportLocalTLSAdapter` 关闭 SSL 校验 | skill_manager.py L95-108 | 已被 `_maybe_disable_insecure_warning` 条件化，需审配置默认 |
| skill 脚本内受限 `eval()` | ascend-moe-optimizer-auto-trace/scripts/trace_utils.py L42 | 空 builtins 解析 C 宏,风险低;建议 `ast.literal_eval` |

---

## 7. 亮点

1. **严格的路径校验**：`_safe_path_name` / `_safe_child_path` / `_safe_rmtree` 三件套防 path traversal，体现安全工程化思维。
2. **优雅降级**：`_maybe_disable_insecure_warning` 从"全局静默"改为"按开关条件触发"（L85-92 注释明确历史变更），这是真实 issue-driven refactor。
3. **跨实例共享 dict 的显式注释**：`_SKILLNET_INSTALL_JOBS` 模块全局 dict 在 L54-56 解释了"为什么用模块全局而非实例属性"（防止 `skills.*` 无状态 RPC 落到不同实例）—— 这种"非常规设计必须配文档"的纪律值得肯定。
4. **测试即文档**：每个测试文件可作为被测模块的使用示例。
5. **git 子进程封装**： `_git_clone` / `_git_pull` / `_git_get_commit` 用 `asyncio.create_subprocess_exec`（非阻塞）而非 `subprocess.run`（阻塞），整体架构是 async 友好的。
6. **（v2 新增）会话资源主动治理**：`_evict_idle_session_adapters()` 的 TTL 驱逐设计（2h TTL / 批量上限 3 / 锁保护 / 活跃检测 / waiter 检查）+ 11 处关键路径 finally 触发,是长跑稳定性的典范实现——初版评审误判为缺失,特此更正并列为亮点。
7. **（v2 新增）工具输出截断有留痕**：`_truncate_team_tool_result_event` 截断时附 `truncated: True` 与 `original_size`,前端/日志可感知截断发生——初版误判为"无标记",更正。
8. **（v2 新增）RAG 增量向量化**：`add_documents` 只向量化新块,显式避免 O(N²) API 成本（代码注释自证设计意图）——初版误判为"无增量",更正。

---

## 8. 优先级修复建议（v2 修订）

**本周内**：
- H1 git 子进程 timeout（影响所有 marketplace 用户的可用性）
- H2/H3 git 异常捕获收紧 + TimeoutExpired 单独处理（影响排障效率）

**本月内**：
- M1 interface_deep.py 拆分（下次大改前的预防性 refactor）
- M2 _SKILLNET_INSTALL_JOBS 上限
- M5 补"降级全报告过 Reviewer"端到端断言

**下季度**：
- L1 测试目录合并（unit → unit_tests）
- L2 pylint 规则 + 自定义 AST checker 治理 `except Exception: pass`（项目级 298 文件）
- L4 skill-omni-creation sudo 行为确认化
- 性能维度：`tests/perf/` benchmark 基线 + `_sync_marketplace_repos` 并发化

---

## 9. 总结

JiuwenSwarm 是一个**生产可用、工程化扎实、有清晰扩展面的多 Agent 框架**。v2 复核后,有效问题 13 项（C 0 / H 3 / M 5 / L 5,其中 L3 已确认解决留档）,均不阻断功能,但在企业级大流量、长运行场景下会逐步显形。修复优先级 H1/H2/H3 是"必做"；M 系列是"应做"；L 系列是"机会型清理"。

**最终评级**（v2 修订——初版因误判 session 治理缺失而压低性能分,复核后上调）：

- 架构与可维护性：⭐⭐⭐⭐☆（4/5）—— 接口文件过大是显著扣分项
- 测试与质量：⭐⭐⭐☆☆（3/5）—— 单元测试密度高（12.2 万行）,但集成测试仅 2 文件、端到端断言缺失,覆盖结构失衡
- 性能与资源：⭐⭐⭐⭐☆（4/5）—— 会话治理/增量 RAG/并发检索均到位;扣分在 git 无超时、无 benchmark、install jobs 无上限
- 综合：⭐⭐⭐⭐☆（4/5）—— 框架层足够稳,建议在下一季度做一次专项治理（接口拆分 + 宽异常治理 + 集成测试补齐）。

---

## 附：v2 复核修订记录（评审的评审）

> 复核方法：对初版评审的全部可验证断言逐条对照源码/`git show` 重查;所有 grep 改用 `find -print0 | xargs -0` 避免 Windows xargs 分批截断。

| # | 初版断言 | 复核结论 | 处置 |
|---|---|---|---|
| 1 | 核心代码 8.0 万行 | **误计**（xargs 分批,`tail -1` 只取了第 3 批 80,004;三批合计 288,826） | 全文更正为 28.9 万行;"测试:核心 1.5:1"更正为 0.42:1 |
| 2 | H3"session adapter 无 LRU/TTL,长跑 OOM" | **证伪**——`_evict_idle_session_adapters()` 完备（TTL 2h/批量 3/锁/活跃检测,11 处触发点） | 删除 H3,原 H4 升为 H3;机制列为亮点 #6 |
| 3 | M6"`_session_adapter_last_used` 无消费者" | **证伪**——L1451 驱逐逻辑消费它 | 删除 M6 |
| 4 | M4"截断未保留关键信号" | **部分证伪**——`truncated`/`original_size` 标记存在;剩余问题仅"截尾部丢堆栈" | 降级改写,保留为 M4 |
| 5 | 性能表"Skill 检索串行" | **证伪**——`handle_skills_online_search` L1122 已 `asyncio.gather` 并发 | 表中更正为 ✅ |
| 6 | 性能表"RAG 无增量" | **证伪**——L233-234 注释+实现均增量 | 表中更正为 ✅ |
| 7 | "TODO/FIXME 0 处,干净" | **误检**（漏 resources/）——实际 54 处（多为 prompt 常量） | 气味表更正 |
| 8 | "eval/exec 0 处,干净" | **误检**——ascend skill L42 有受限 `eval()` | 气味表更正 + 安全表新增一行 |
| 9 | "integration ~10 个文件" | **误计**——实际 2 个文件,错位比初版估计更严重（1:143） | 4.1 更正,测试评级 4→3 |
| 10 | "finance 测试 8 个文件" | **误计**——实际 15 文件/174 测试函数（评审当时只看了 README 描述） | 4.1 更正 |
| 11 | "DeepAdapter 方法 80+" | **低估**——awk 精确统计 293 个方法 | M1 更正 |
| 12 | M5/L3"需查 27694f2 是否修复" | **已确认**——M1-M4/L1-L3 全部修复 + 11 回归用例;宏观 mock 已升级乱序多期 | M5 改写为"已落地,仅缺端到端 Reviewer 断言";L3 标已解决留档 |
| 13 | L4"sudo 自动提权" | **确认存在但描述过重**——用的是 `sudo -n`（non-interactive）,不会挂起;风险是静默提权尝试而非交互劫持 | L4 保留,风险措辞弱化 |

**复核结论**:初版 14 项中 2 项整项证伪、3 项部分证伪/改写、5 处统计误计、3 处误检"干净"、1 项从"待查"落定为"已解决"。v2 有效问题 13 项。教训:①grep 统计必须防 xargs 分批;②"未读到实现"的推测（M4 初版自认"未在文件中可见"）必须先实读再定级;③"会话无治理"这类否定性断言,证伪成本远低于证实,应优先搜索反例。

---

## 附 2：修复记录（2026-08-18）

| # | 问题 | 处置 | 落地方式 |
|---|---|---|---|
| H1 | git 子进程无 timeout | ✅ 已修 | `_run_git_subprocess` 统一封装 `asyncio.wait_for`（默认 30s，`SKILLNET_GIT_TIMEOUT` 可调）+ 超时 kill；`_sync_marketplace_repos` 改 `asyncio.gather` 并发且单个失败隔离 |
| H2/H3 | `_run_git` 宽异常吞错/三态不可区分 | ✅ 已修 | `TimeoutExpired` 单独 warning 留痕；环境类异常 debug、子进程/编码异常 warning；超时可经 `RUNTIME_GIT_TIMEOUT` 调整 |
| M2 | `_SKILLNET_INSTALL_JOBS` 无上限 | ✅ 已修 | 容量上限 1000（`SKILLNET_MAX_INSTALL_JOBS`），写入统一走 `_set_install_job`，超限优先驱逐终态（done/failed）旧记录 |
| M3 | `_safe_rmtree` 失败静默 | ✅ 已修 | 新增 `_rmtree_or_fail`：强装/重建路径删除失败即快速失败返回统一错误（`skills.common.errors.removeFailed`），不再继续 copytree 留部分目录 |
| M4 | 截断丢尾部堆栈 | ✅ 已修 | `_truncate_team_tool_result_text` 改头 70% + 尾 30% + `...[truncated N chars]...` 标注 |
| M5 | 缺降级端到端断言 | ✅ 早已存在 | `TestDegradedReportPassesReviewer::test_offline_degraded_draft_passes_review`（评审基线后 finance 测试已增至 212+） |
| L4 | 自动 sudo 提权 | ✅ 已修 | 默认禁用，需 `OMNI_GATE_ALLOW_SUDO=1` 或交互终端确认；失败原因写入 `environment_status.json` 与报错文案；SKILL.md 同步更新 |
| M1 | interface_deep.py 拆分 | ⏸ 留档 | 1.18 万行/293 方法的接口层拆分属高风险大重构，需专项排期 |
| L1 | tests/unit 并入 unit_tests | ⏸ 留档 | 下季度目录治理一并处理 |
| L2 | 宽异常项目级治理 | ⏸ 留档 | 需 pylint 规则 + AST checker 专项（298 文件） |

回归测试：`tests/unit_tests/agentserver/test_review_fixes.py`（13 用例；框架层用例依赖 openjiuwen，仅 CI 环境执行，本机自动 skip）。