# 代码评审报告（Day 3）

> 评审日期：2026-08-17
> 评审范围：main 分支 HEAD（348c0a2）— finance-report 技能全部源码 + 单测 + 600519.md 交付物
> 评审方式：CodeReview 子代理专业评审（实测复现 + 165 个单测回归验证）
> 严重程度分级：C（阻断提交）/ H（应尽快修复）/ M（建议修复）/ L（可选优化）

---

## 1. 总体评价

整体架构清晰（五 Agent 链式编排 + 采集/分析/生成三层分离 + LLM 优先/规则降级双路径），测试密度与断言质量在同类比赛项目中属上乘：165 个单测全部通过，且上一轮 14 项问题（假同比、None 语义、沙箱绕过、权重末位收尾等）均有参数化回归用例真实锁定行为。但本轮实测发现一个 **Critical 级数据正确性缺陷**：宏观分析器从 akshare 取到的是 **2006/2008 年的历史数据**（`iloc[-1]` 取反 + 列名关键词写错），导致提交报告 600519.md 中出现 "CPI 7.08%、PMI -0.33" 这类不可能的数值；另有降级路径报告无法通过自研引用闸门、估值章节出现编造数值（PE 21 倍/"Wind 一致预期"，而管线中 PE 永远算不出）、图文一致性检查为空实现等高危问题。这些恰好命中赛题"事实准确、可溯源、禁止编造"的评分核心，**建议提交前务必修复 C1/H1/H2**。

---

## 2. 问题清单

### Critical（阻断提交）

#### C1. 宏观指标取到 2006/2008 年历史数据，PMI/CPI 数值完全错误
位置：`analyzers/macro_analyzer.py` `_fetch_series`（L20-24, L95-122）

**问题**：两个叠加错误，均经真实 akshare 接口实测复现：
1. **行序假设反了**：`last = df.iloc[-1]` 假设"最后一期为最新值"，但 akshare `macro_china_*` 系列是**新→旧**排序，`iloc[-1]` 取到的是最旧一期——实测 PMI 末行为 `2008年01月份`、GDP 末行为 `2006年`。
2. **PMI 列关键词不存在**：`INDICATOR_SOURCES` 写的是 `"制造业-当月值"`，真实列名是 `"制造业-指数"`，匹配失败后走"取最后一个数值列"兜底，实际命中 `非制造业-同比增长`。

**影响**：提交物 `reports/finance-report/个股投资研报/600519.md` 出现"CPI为7.08%…PMI为-0.33"——正是 2008 年 1 月的 `全国-同比增长=7.0781` 与 `非制造业-同比增长=-0.3311`（逐一吻合）。PMI 为负数在定义上不可能，直接违反"禁止编造/数据可信"约束，且这些错误值已被注入行业分析与估值章节的论证链。

**修复方向**：
```python
df = getattr(ak, spec["func"])()
df = df.sort_values(df.columns[0])   # 按时间列显式升序，勿依赖接口行序
last = df.iloc[-1]
```
同时把 PMI 列关键词改为真实列名 `"制造业-指数"`，并加值域健全性校验（如 PMI∈[30,70]、CPI∈[-5,10]），越界视为取错列、降级跳过；`test_macro_analyzer` 的 mock 需改为**多期且乱序**的 DataFrame 并断言取到最新期（当前单行 mock 对这两个 bug 完全失明）。

### High（应尽快修复）

#### H1. 降级（无 LLM）报告无法通过自研引用闸门，自检循环必然失败
位置：`generators/report_writer.py` `SECTION_SOURCE`/`_template_section`（L52-57, L211-234）

**问题**：`SECTION_SOURCE` 只给三/四/五/六章固定来源标注；一（核心观点）、二（投资结论）、七（风险提示）的规则模板段从不输出"数据来源"行，而这三段模板里恰恰含大量数据句（洞察含 %、仓位区间 5%-10% 等）。实测（离线降级全流程 + `ReviewerAgent.review`）：**引用率仅 30%（3/10），审查不通过（score 90、闸门 issue）**。由于 `supplement` 是空操作且降级输出确定性相同，编排器空转 3 轮后仍以 `passed_review=False` 收尾，Investor 打 0 分输出空仓。模块 docstring"结构不缺、数据同源、**来源不缺**"的承诺被打破。

**影响**：任何无 API Key / LLM 不可用的运行（含单测所代表的离线路径）产出的报告都过不了项目自己的验收闸门；且现有测试无"降级全报告必须过 Reviewer"的端到端断言，故此缺陷未被暴露。

**修复**：为一、二、七章补固定来源标注，例如：
```python
SECTION_SOURCE.update({
    "一、核心观点": "公司定期财报与公开行情数据",
    "二、投资结论与仓位建议": "公司定期财报与公开行情数据",
    "七、风险提示": "公司公告与权威财经媒体报道",
})
```
并建议补一条端到端用例：降级生成的完整 draft 经 `ReviewerAgent().review` 必须 `passed`。

#### H2. 估值章节编造已实锤：PE 在管线中永远不可得，报告却写"PE约21倍（Wind一致预期）"
位置：`analyzers/finance_analyzer.py` `_calc_pe`（L154-172）+ `collectors/quote_collector.py` + 600519.md L57

**问题**：`_calc_pe` 需要 `market_cap` 或 `latest_close + total_shares`，但 QuoteCollector 从不采集市值/总股本（落盘 quote JSON 实测确认只有行情序列）→ PE 恒为 None → 估值章节材料中 `估值指标: {}`。然而提交报告写着"采用 **Wind 一致预期** 下 2026 年盈利预测…对应 **PE 约 21 倍**"——管线中不存在 Wind 数据，也没有任何 PE 数值，属 LLM 在无材料支撑下编造数字与来源，且 Reviewer 无任何机制拦截正文中材料之外的数字。

**影响**：赛题明令"禁止编造、所有数据可溯源"，该句一旦被评委核查即为硬伤；这是系统性缺口（材料为空仍照常生成章节 + 无正文-材料数值一致性校验）而非偶发幻觉。

**修复方向**：① QuoteCollector 增补市值/总股本采集（如 akshare `stock_individual_info_em`），使 PE 真实可算；② 材料中估值为空时，章节 prompt/模板强制输出"暂无可溯源估值数据"并禁止给出数字；③ 结合 M1 补正文数值与材料的一致性抽查。

### Medium（建议修复）

#### M1. Reviewer 图文一致性检查为空实现（死代码）
位置：`agents/reviewer.py` `_check_chart_text_consistency`（L102-112）

**问题**：循环读 `getattr(chart, "text_mentions", [])` 与 `chart.data_value`，但 `Chart` dataclass 根本没有这两个字段 → 内层循环永远不执行，检查恒通过；若真有了 `text_mentions`，`chart.data_value` 还会立刻 AttributeError。"图文一致"检查由此落空，且给出虚假安全感。

**修复**：基于现有字段实现（如用正则抽取 `chart.caption` 与正文中的数值做比对），或明确删除该检查并标 TODO，不要保留看似生效的死代码。

#### M2. Investor 分散度约束 min_position_count 读取后从未使用
位置：`agents/investor.py` `__init__`/`_allocate`（L35-37, L92-122）

**问题**：`self.min_positions` 仅在构造时赋值，`_allocate` 全程未引用——只要 1 只标的过阈值即可输出单票组合，docstring 宣称的风控约束（`min_position_count: 3`）未生效。

**修复**：`len(valid) < min_positions` 时要么阐明理由空仓（复用决策日志），要么明确这是软约束并在注释/文档中降级表述。

#### M3. Prompt 占位符"本文首段"泄漏进提交报告正文
位置：`generators/report_writer.py` `_write_section`（L175）

**问题**：首段 `context or '（本文首段）'` 被 LLM 原样复述，600519.md 正文第一行即孤立的"本文首段"，`_normalize_section` 只剥离重复标题、不清理此类回声。

**修复**：`_normalize_section` 增加对占位符行（如完全等于"本文首段"）的过滤；或将占位文案改为更不易被复述的指令形式。

#### M4. 风险提示材料无条件注入无据结论"板块竞争加剧"
位置：`generators/report_writer.py` `_materials_for`（L304-310）

**问题**：`"行业负面"` 在板块景气非"景气承压"时**一律**喂给 LLM "板块竞争加剧"——该判断从未被任何数据计算过（竞争格局数据里并无"竞争加剧"指标）。对景气向上的板块，这是结构性编造诱导；`"宏观提示": "宏观指标波动风险"` 同理为固定文案。

**修复**：依据 `prosperity.level`/`competition.target_rank` 实际取值措辞；无依据时给"未见显著行业负面信号"，让 LLM 有如实表达的出口。

### Low（可选优化）

- **L1.** `generators/citation_checker.py` L41-43：`min_rate` 参数未参与 `passed` 判定（硬编码 0.9 且要求 `issues==0`，等效 100% 引用）；Reviewer 闸门用 `min_rate`、`passed` 用硬编码，两套口径不一致。
- **L2.** `generators/chart_generator.py` L169, L210-211：折线图 `close or 0`、柱状图缺失指标补 0 绘制，与全链路 None 语义相悖（个别期缺 ROE 会画出"ROE=0"的假柱）。建议缺失点不绘制（`np.nan`）。
- **L3.** `run_report.py` L53-55：`--output-dir` 定义在主 parser 上，必须置于子命令之前，否则报 unrecognized arguments，与示例用法直觉不符；建议同时挂到各子命令。

**测试有效性结论**：整体断言真实锁定行为（假同比、None 语义、沙箱绕过 payload、引用口径、权重残差均有回归），但存在两处与上述缺陷直接相关的盲区：① `test_macro_analyzer` 用单行、列名恰好命中关键词的定制 mock，锁不住"取最新期/真实列名"（C1 漏网）；② 缺少"降级全报告必须通过 Reviewer"的端到端断言（H1 漏网）。

---

## 3. 亮点

1. **None 语义全链路贯彻且有回归锁定**：`FinancialStatement` 缺失即 None，洞察规则全部 `is not None` 门控，"ROE 缺失不解读为回报偏弱"有专门回归用例。
2. **同比口径严格对齐上年同期**：`_yoy_prev` 拒绝回退上一期（避免 A 股累计口径 Q1 vs Q4 的 -87% 假同比），并有 `test_missing_yoy_no_fake_growth` 锁定。
3. **沙箱绕过向量回归完备**：getattr 字符串 dunder、裸 `__builtins__`、`get_ipython()`、`format` 格式串等评审实测 payload 全部参数化锁定，且类注释诚实说明 AST 白名单的局限与进程隔离建议。
4. **引用闸门口径自洽**：数据句按行计 + 段落级覆盖 + 表格/标题/图片行剔除，与 Writer"章末来源标注"约定严格对齐，正/反例测试齐全。
5. **图文同源与图片本地化真实有效**：图表程序化注入、失效/外链图片移除并留痕 `image_issues`；采集层多源降级（akshare→腾讯→新浪）+ `source/collected_at/search_trace` 全程留痕，可复现性好。
