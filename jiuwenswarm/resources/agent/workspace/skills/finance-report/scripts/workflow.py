# -*- coding: utf-8 -*-
"""Executable SwarmFlow workflow for finance-report.

全流程封装为确定性五阶段工作流（Day 5 任务 1）：
选股 → 采集 → 分析 → 决策 → 报告

- 状态传递：采集产物落盘缓存（data/）→ 分析阶段读缓存评分
  （scores_cache.json）→ 决策阶段复用评分 → 报告阶段只为入选标的
  生成研报
- 错误重试：每个阶段的 agent 调用自动重试一次（MAX_ATTEMPTS 有限
  终止），单板块/单标的失败不阻断整体（pipeline 式扇出 + compact）
- 确定性：脚本内无随机源、无时间读取、无直接文件 IO；全部落地动作
  由 worker agent 经技能 CLI（run_report.py）执行
"""
import json
from string import Template

from swarmflow import agent, compact, log, phase, pmap

META = {
    "name": "finance-report",
    "description": "金融分析全流程确定性工作流：选股→采集→分析→决策→报告五阶段，状态落盘传递，失败自动重试一次",
    "whenToUse": "需要端到端跑通公司池批量投资决策与研报产出时（全流程联调、最终 Portfolio.json 交付）",
    "phases": [
        {"title": "选股", "detail": "校验公司池白名单，枚举六大板块与标的清单"},
        {"title": "采集", "detail": "按板块逐标的采集行情/财报/新闻（缓存优先，断点续采）"},
        {"title": "分析", "detail": "读已采集数据跑分析引擎并因子打分，评分缓存落盘"},
        {"title": "决策", "detail": "复用评分缓存做风控约束仓位配置，产出 Portfolio.json 与决策日志"},
        {"title": "报告", "detail": "为入选标的逐个生成个股投资研报（股票代码.md）"},
    ],
}

# ---------------------------------------------------------------------------
# 常量（全大写：模块级不可变配置，非运行状态）
# ---------------------------------------------------------------------------
SKILL_DIR = "skills/finance-report"
DEFAULT_POOL_FILE = "example/上市公司列表.xlsx"
MAX_ATTEMPTS = 2       # 错误重试上限：首次 + 重试一次（有限终止）
MAX_FANOUT = 12        # 采集扇出上限（六大板块 + 余量），超出跳过并留痕
MAX_REPORTS = 16       # 报告扇出上限（入选标的余量），超出跳过并留痕

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {"type": "string"},
        "verdict": {"type": "string"},
        "sectors": {"type": "array", "items": {"type": "string"}},
        "portfolio": {"type": "object"},
    },
}

# ---------------------------------------------------------------------------
# 提示词模板（string.Template，避免 JSON 示例中的花括号被解释）
# ---------------------------------------------------------------------------
_CLI_HINT = (
    f"执行方式：先进入技能目录 {SKILL_DIR}（工作区根目录下的相对路径），"
    "然后在技能根目录内执行给出的 python run_report.py 命令。"
    "命令的标准输出即结果 JSON。"
)

SELECT_PROMPT = Template(
    """你是 finance-report 技能的「选股」阶段执行者。

$_cli_hint

命令：
    python run_report.py pool --pool-file "$pool_file"

该命令输出公司池 JSON（sectors 板块清单 / symbols 各板块标的 / total 总数）。
请核对命令成功执行后，只返回一个 JSON 对象，不要输出其他内容：
{"result": "命令输出的JSON原文", "verdict": "ok", "sectors": ["板块1", "板块2"], "portfolio": {}}
命令执行失败时返回 {"result": "错误摘要", "verdict": "failed", "sectors": [], "portfolio": {}}
"""
)

COLLECT_PROMPT = Template(
    """你是 finance-report 技能的「采集」阶段执行者，负责板块「$sector」。

$_cli_hint

命令：
    python run_report.py research --stage collect --pool-file "$pool_file" --sector "$sector"

该命令按标的采集行情/财报/新闻并落盘缓存（缓存优先，单标的失败自动重试一次后跳过）。
命令结束后，只返回一个 JSON 对象，不要输出其他内容：
{"result": "命令输出的JSON原文", "verdict": "ok", "sectors": [], "portfolio": {}}
命令执行失败时返回 {"result": "错误摘要", "verdict": "failed", "sectors": [], "portfolio": {}}
"""
)

ANALYZE_PROMPT = Template(
    """你是 finance-report 技能的「分析」阶段执行者。

$_cli_hint

命令：
    python run_report.py research --stage analyze --pool-file "$pool_file" --save

该命令读已采集的落盘数据跑分析引擎并做因子打分（确定性规则，不走 LLM），
评分缓存写入 decision_log/scores_cache.json。
命令结束后，只返回一个 JSON 对象，不要输出其他内容：
{"result": "命令输出的JSON原文", "verdict": "ok", "sectors": [], "portfolio": {}}
命令执行失败时返回 {"result": "错误摘要", "verdict": "failed", "sectors": [], "portfolio": {}}
"""
)

DECIDE_PROMPT = Template(
    """你是 finance-report 技能的「决策」阶段执行者。

$_cli_hint

命令：
    python run_report.py invest --pool-file "$pool_file" --use-cached-scores --skip-reports --max-positions 8 --save

该命令复用分析阶段评分缓存（缺失时自动回退实时采集分析），按风控约束
（单标的权重 ≤ 0.4、总权重 ≤ 1.0、白名单硬校验、达标过多取评分前 8）
产出 Portfolio.json 与决策日志 decision_log/decision.json。标准输出末尾即 Portfolio JSON。
命令结束后，只返回一个 JSON 对象，不要输出其他内容：
{"result": "命令输出的Portfolio JSON原文", "verdict": "ok", "sectors": [], "portfolio": {"股票代码": 权重}}
命令执行失败时返回 {"result": "错误摘要", "verdict": "failed", "sectors": [], "portfolio": {}}
"""
)

REPORT_PROMPT = Template(
    """你是 finance-report 技能的「报告」阶段执行者，负责标的 $symbol。

$_cli_hint

命令：
    python run_report.py company --target $symbol --save

该命令端到端生成该标的个股投资研报（撰写 + 审查回流 ≤ 2 轮），
落盘 reports/finance-report/个股投资研报/$symbol.md。
（L6 说明：命令不带 --name，公司名由行情缓存/公司池自动回填，
工作流「先采集后报告」顺序下采集缓存必然已含 name 字段。）
命令结束后，只返回一个 JSON 对象，不要输出其他内容：
{"result": "审查结论与研报保存路径", "verdict": "ok", "sectors": [], "portfolio": {}}
命令执行失败时返回 {"result": "错误摘要", "verdict": "failed", "sectors": [], "portfolio": {}}
"""
)


# ---------------------------------------------------------------------------
# 弹性辅助函数
# ---------------------------------------------------------------------------
def extract_json(text, fallback=None):
    """从 agent 输出中提取首个 JSON 对象（可能夹带说明文字）"""
    fallback_value = {} if fallback is None else fallback
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return fallback_value
    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else fallback_value
    except (json.JSONDecodeError, ValueError):
        pass
    depth = 0
    start = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}' and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    parsed = json.loads(text[start:i + 1])
                    return parsed if isinstance(parsed, dict) else fallback_value
                except (json.JSONDecodeError, ValueError):
                    start = None
    return fallback_value


def safe_get(obj, key, default=""):
    """安全取键：处理 None / 非 dict"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def parse_args(args):
    """归一化 args 为 dict（Swarmflow 可能传 JSON 字符串）"""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {}
    return {}


_FAILED = {"result": "agent_failed", "verdict": "failed",
           "sectors": [], "portfolio": {}}


async def call_with_retry(prompt, phase_title, label, timeout):
    """错误重试封装：有限重试 MAX_ATTEMPTS 次（for-range 明确终止），
    verdict=ok 即成功返回；仍失败返回最后一次结果由调用方降级处理。
    """
    result = dict(_FAILED)
    for attempt in range(MAX_ATTEMPTS):
        # L1 修复：agent 调用异常（超时等）也纳入重试路径，
        # 不让异常穿透重试封装中断 pmap 扇出
        try:
            raw = await agent(
                prompt, label=label, phase=phase_title,
                schema=RESULT_SCHEMA, options={"timeout": timeout})
        except Exception as e:  # noqa: BLE001
            log(f"{label} 第 {attempt + 1} 次调用异常: {str(e)[:200]}")
            raw = None
        result = extract_json(raw, fallback=dict(_FAILED))
        if safe_get(result, "verdict", "failed") == "ok":
            return result
        action = "重试一次" if attempt + 1 < MAX_ATTEMPTS else "降级跳过"
        log(f"{label} 第 {attempt + 1} 次未成功: "
            f"{str(safe_get(result, 'result', ''))[:200]}，将{action}")
    return result


# ---------------------------------------------------------------------------
# 工作流入口
# ---------------------------------------------------------------------------
async def run(args):
    """执行五阶段全流程，返回 JSON 可序列化结果"""
    args = parse_args(args)
    pool_file = str(args.get("pool_file") or DEFAULT_POOL_FILE)
    only_sector = str(args.get("sector") or "")

    async def retry_select():
        for attempt in range(MAX_ATTEMPTS):
            raw = await agent(
                SELECT_PROMPT.substitute(
                    _cli_hint=_CLI_HINT, pool_file=pool_file),
                label="select", phase="选股",
                schema=RESULT_SCHEMA, options={"timeout": 600})
            result = extract_json(raw, fallback=dict(_FAILED))
            if safe_get(result, "verdict", "failed") == "ok":
                return result
            log(f"select 第 {attempt + 1} 次未成功，"
                f"{'重试一次' if attempt + 1 < MAX_ATTEMPTS else '降级终止'}")
        return dict(_FAILED)

    async def retry_collect(sector):
        return await call_with_retry(
            COLLECT_PROMPT.substitute(
                _cli_hint=_CLI_HINT, pool_file=pool_file, sector=sector),
            "采集", "collect", timeout=1800)

    async def retry_analyze():
        return await call_with_retry(
            ANALYZE_PROMPT.substitute(
                _cli_hint=_CLI_HINT, pool_file=pool_file),
            "分析", "analyze", timeout=1800)

    async def retry_decide():
        return await call_with_retry(
            DECIDE_PROMPT.substitute(
                _cli_hint=_CLI_HINT, pool_file=pool_file),
            "决策", "decide", timeout=1800)

    async def retry_report(symbol):
        return await call_with_retry(
            REPORT_PROMPT.substitute(
                _cli_hint=_CLI_HINT, symbol=symbol),
            "报告", "report", timeout=1800)

    # ---- 阶段1 选股：公司池白名单校验与板块枚举 ----
    phase("选股")
    log(f"开始选股：校验公司池 {pool_file}")
    select = await retry_select()
    sectors = [str(s) for s in (safe_get(select, "sectors", []) or [])]
    if only_sector:
        sectors = [s for s in sectors if s == only_sector] or [only_sector]
    if safe_get(select, "verdict", "failed") != "ok" or not sectors:
        log(f"选股失败或公司池为空: {str(safe_get(select, 'result', ''))[:200]}")
        return {"status": "failed", "failed_phase": "选股",
                "detail": str(safe_get(select, "result", ""))[:500]}
    log(f"公司池校验通过：板块 {sectors}")

    # ---- 阶段2 采集：按板块扇出（失败自动重试，单板块失败不阻断） ----
    phase("采集")
    skipped = sectors[MAX_FANOUT:]
    if skipped:
        log(f"扇出上限 {MAX_FANOUT}，跳过板块: {skipped}")
    collect_results = compact(await pmap(
        sectors[:MAX_FANOUT], retry_collect))
    collect_ok = [
        r for r in collect_results
        if safe_get(r, "verdict", "failed") == "ok"]
    log(f"采集完成：成功 {len(collect_ok)}/{len(sectors[:MAX_FANOUT])} 个板块"
        f"（缓存落盘 data/，供分析阶段复用）")

    # ---- 阶段3 分析：读缓存跑分析引擎并因子打分（评分缓存落盘） ----
    phase("分析")
    analyze = await retry_analyze()
    if safe_get(analyze, "verdict", "failed") != "ok":
        log("分析阶段失败，决策阶段将回退实时采集分析（CLI 内置降级）")

    # ---- 阶段4 决策：复用评分缓存产出 Portfolio.json ----
    phase("决策")
    decision = await retry_decide()
    portfolio = safe_get(decision, "portfolio", {}) or {}
    if not isinstance(portfolio, dict):
        portfolio = {}
    if safe_get(decision, "verdict", "failed") != "ok":
        log(f"决策失败: {str(safe_get(decision, 'result', ''))[:200]}")
        return {"status": "failed", "failed_phase": "决策",
                "detail": str(safe_get(decision, 'result', ''))[:500],
                "sectors": sectors,
                "collect_ok": len(collect_ok)}
    symbols = [str(s) for s in portfolio.keys()]
    if not symbols:
        log("组合为空仓（决策日志 decision.json 已记录决策逻辑），跳过报告生成")

    # ---- 阶段5 报告：仅为入选标的逐个生成研报 ----
    phase("报告")
    report_results = []
    if symbols:
        skipped_reports = symbols[MAX_REPORTS:]
        if skipped_reports:
            log(f"报告扇出上限 {MAX_REPORTS}，跳过标的: {skipped_reports}")
        targets = symbols[:MAX_REPORTS]
        log(f"为入选标的生成研报: {targets}")
        report_results = compact(await pmap(targets, retry_report))
        report_ok = [
            r for r in report_results
            if safe_get(r, "verdict", "failed") == "ok"]
        log(f"报告完成：成功 {len(report_ok)}/{len(targets)} 份")

    return {
        "status": "complete",
        "sectors": sectors,
        "collect_ok": len(collect_ok),
        "analyze_verdict": safe_get(analyze, "verdict", "failed"),
        "portfolio": portfolio,
        "reports": [
            {"verdict": safe_get(r, "verdict", "failed"),
             "result": str(safe_get(r, "result", ""))[:200]}
            for r in report_results
        ],
    }
