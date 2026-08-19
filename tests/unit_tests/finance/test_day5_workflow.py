# -*- coding: utf-8 -*-
"""Day 5 回归测试：Swarmflow 工作流结构合规 + 遥测 + 批量容错

覆盖三任务验收点：
1. scripts/workflow.py 满足 SwarmFlow 脚本安全包络（META 字面量、
   run 以 parse_args 开头、phase 顶层覆盖、禁非确定性导入/print）
2. telemetry：阶段计时/LLM token 累计/种子固定/run_stats.json 追加
3. investor 批量失败重试一次后跳过留痕；评分缓存状态传递
"""

import ast
import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

# 技能目录与公司池路径（与 conftest.py 口径一致，避免跨包导入 conftest）
_SKILL_DIR = (
    Path(__file__).resolve().parents[3]
    / "jiuwenswarm" / "resources" / "agent" / "workspace" / "skills"
    / "finance-report"
)
SKILL_DIR = _SKILL_DIR
POOL_FILE = Path(__file__).resolve().parents[3] / "example" / "上市公司列表.xlsx"

WORKFLOW_PY = SKILL_DIR / "scripts" / "workflow.py"
PHASE_TITLES = ["选股", "采集", "分析", "决策", "报告"]


def _workflow_tree():
    return ast.parse(WORKFLOW_PY.read_text(encoding="utf-8"))


def _run_node(tree):
    return next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")


class TestWorkflowScriptShape:
    """任务1：workflow.py 结构合规自检（与 validate_swarmskill 对齐）"""

    def test_meta_literal_and_phases(self):
        tree = _workflow_tree()
        meta = next(
            n.value for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "META"
                    for t in n.targets))
        meta = ast.literal_eval(meta)  # 纯字面量才可 eval
        assert meta["name"] == "finance-report"
        assert [p["title"] for p in meta["phases"]] == PHASE_TITLES
        assert all(p["detail"] for p in meta["phases"])

    def test_run_starts_with_parse_args(self):
        run_node = _run_node(_workflow_tree())
        assert [a.arg for a in run_node.args.args] == ["args"]
        body = run_node.body
        # 允许首句为 docstring（校验器同样跳过）
        if (isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        first = body[0]
        assert isinstance(first, ast.Assign)
        assert isinstance(first.value, ast.Call)
        assert isinstance(first.value.func, ast.Name)
        assert first.value.func.id == "parse_args"

    def test_phase_coverage_at_top_level(self):
        """META 声明的每个 phase title 须在 run() 顶层出现 phase()"""
        run_node = _run_node(_workflow_tree())
        called = []
        for stmt in run_node.body:
            if (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id == "phase"
                    and stmt.value.args
                    and isinstance(stmt.value.args[0], ast.Constant)):
                called.append(stmt.value.args[0].value)
        assert called == PHASE_TITLES  # 顺序即工作流阶段顺序

    def test_no_nondeterministic_imports_or_print(self):
        tree = _workflow_tree()
        banned_roots = {"os", "subprocess", "random", "time",
                        "datetime", "asyncio", "skill_context"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned_roots
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned_roots
            elif isinstance(node, ast.Call):
                fn = node.func
                assert not (isinstance(fn, ast.Name) and fn.id == "print")
                # asyncio 编排禁令
                if isinstance(fn, ast.Attribute):
                    assert fn.attr not in ("gather", "create_task")

    def test_log_calls_single_positional_arg(self):
        tree = _workflow_tree()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "log"):
                assert len(node.args) == 1 and not node.keywords

    def test_swarmflow_explicit_imports(self):
        tree = _workflow_tree()
        imported = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "swarmflow"
            for alias in node.names}
        assert {"agent", "phase", "log", "pmap", "compact"} <= imported


class TestWorkflowHelpers:
    """任务1：脚本内弹性辅助函数（importlib 按文件加载，不依赖 swarmflow）"""

    @pytest.fixture(scope="class")
    def wf(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "finance_workflow_standalone", WORKFLOW_PY)
        module = importlib.util.module_from_spec(spec)
        # swarmflow 仅运行时可用：加载前注入桩模块（本测试只测纯函数）
        stub = types.ModuleType("swarmflow")
        for name in ("agent", "compact", "log", "phase", "pmap"):
            setattr(stub, name, lambda *a, **k: None)
        sys.modules["swarmflow"] = stub
        try:
            spec.loader.exec_module(module)
            yield module
        finally:
            sys.modules.pop("swarmflow", None)

    def test_parse_args_dict_and_json_string(self, wf):
        assert wf.parse_args({"sector": "消费板块"}) == {"sector": "消费板块"}
        assert wf.parse_args('{"sector": "消费板块"}') == {"sector": "消费板块"}
        assert wf.parse_args("not json") == {}
        assert wf.parse_args(None) == {}

    def test_extract_json_embedded_in_prose(self, wf):
        text = '前置说明 {"result": "ok", "verdict": "ok"} 后置说明'
        assert wf.extract_json(text)["verdict"] == "ok"
        assert wf.extract_json(None, fallback={"verdict": "failed"})[
            "verdict"] == "failed"
        assert wf.extract_json({"verdict": "ok"})["verdict"] == "ok"

    def test_call_with_retry_succeeds_second_attempt(self, wf):
        """错误重试：首次失败自动重试一次即成功（有限终止）"""
        calls = []

        async def fake_agent(prompt, *, label=None, phase=None,
                             schema=None, options=None):
            calls.append(label)
            if len(calls) == 1:
                return None  # 模拟 schema 校验失败
            return '{"result": "ok", "verdict": "ok"}'

        wf.agent = fake_agent
        result = asyncio.run(wf.call_with_retry(
            "p", "采集", "collect", timeout=60))
        assert result["verdict"] == "ok"
        assert calls == ["collect", "collect"]  # 恰好重试一次

    def test_call_with_retry_gives_up_after_max_attempts(self, wf):
        calls = []

        async def fake_agent(prompt, *, label=None, phase=None,
                             schema=None, options=None):
            calls.append(1)
            return '{"result": "boom", "verdict": "failed"}'

        wf.agent = fake_agent
        result = asyncio.run(wf.call_with_retry(
            "p", "决策", "decide", timeout=60))
        assert result["verdict"] == "failed"
        assert len(calls) == wf.MAX_ATTEMPTS  # 有限重试不无限循环


class TestTelemetry:
    """任务2：遥测（阶段耗时 / Token 消耗 / 种子 / run_stats 落盘）"""

    def test_seed_fixed(self):
        from common.telemetry import SEED, fix_random_seed
        assert fix_random_seed() == SEED

    def test_summary_shape(self):
        from common.telemetry import RunStats
        stats = RunStats()
        with stats.time_phase("采集"):
            pass
        stats.add_llm_usage({"input_tokens": 10, "output_tokens": 5})
        stats.add_llm_usage(None)  # 非 dict 忽略
        stats.record_failure("research:600519", "boom")
        summary = stats.summary()
        assert summary["seed"]
        assert summary["phases"][0]["phase"] == "采集"
        assert summary["phases"][0]["seconds"] >= 0
        assert summary["llm"] == {
            "calls": 1, "input_tokens": 10, "output_tokens": 5}
        assert summary["failures"][0]["where"] == "research:600519"

    def test_phase_timer_propagates_exception_and_records(self):
        from common.telemetry import RunStats
        stats = RunStats()
        with pytest.raises(ValueError):
            with stats.time_phase("决策"):
                raise ValueError("x")
        assert stats.phases[-1]["error"] is True
        assert stats.failures[-1]["where"] == "决策"

    def test_save_appends_runs(self, tmp_path):
        from common.telemetry import RunStats
        s1 = RunStats()
        s1.add_llm_usage({"input_tokens": 1, "output_tokens": 1})
        path = s1.save(str(tmp_path))
        s2 = RunStats()
        s2.save(str(tmp_path))
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert len(data["runs"]) == 2  # 追加而非覆盖

    def test_llm_client_accumulates_usage(self, monkeypatch):
        """LLMClient.chat 提取响应 usage 字段累计到遥测单例"""
        from common.llm_client import LLMClient
        from common.telemetry import RunStats
        import common.telemetry as telemetry_mod

        stats = RunStats()
        monkeypatch.setattr(telemetry_mod, "RUN_STATS", stats)

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "content": [{"type": "text", "text": "正文"}],
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                }

        class _Session:
            trust_env = True

            def post(self, *a, **k):
                return _Resp()

        fake_requests = types.ModuleType("requests")
        fake_requests.Session = _Session
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        client = LLMClient({"API_KEY": "test", "API_BASE": "http://x"})
        assert client.chat("hi") == "正文"
        assert stats.llm == {
            "calls": 1, "input_tokens": 11, "output_tokens": 7}


class TestInvestorBatchRetry:
    """任务3：批量失败重试一次后跳过留痕 + 评分缓存状态传递"""

    def _pool_symbols(self):
        from collectors.pool_loader import load_pool, whitelist_symbols
        pool = load_pool(str(POOL_FILE))
        return pool, whitelist_symbols(pool)

    def test_failed_symbol_retried_once_then_skipped(self):
        from agents.investor import InvestorAgent
        from common.telemetry import RUN_STATS

        pool, _ = self._pool_symbols()
        first = next(iter(pool.values()))[0]  # 首个板块首个标的
        calls = {first[0]: 0}

        def flaky_research(symbol, name):
            if symbol == first[0]:
                calls[symbol] += 1
                raise RuntimeError("采集失败")
            return {}  # 其余标的空数据 0 分

        before = len(RUN_STATS.failures)
        investor = InvestorAgent()
        portfolio = investor.run_portfolio(
            str(POOL_FILE), save=False, research_fn=flaky_research)
        assert calls[first[0]] == 2  # 恰好重试一次（共 2 次）
        assert isinstance(portfolio, dict)
        assert any(first[0] in n for n in investor.decision_notes)  # 留痕
        assert any(
            f["where"] == f"research:{first[0]}"
            for f in RUN_STATS.failures[before:])

    def test_transient_failure_recovers_on_retry(self):
        from agents.investor import InvestorAgent

        pool, _ = self._pool_symbols()
        first = next(iter(pool.values()))[0]
        attempts = {first[0]: 0}

        def flaky_research(symbol, name):
            if symbol == first[0]:
                attempts[symbol] += 1
                if attempts[symbol] == 1:
                    raise RuntimeError("瞬时失败")
                return {"quote_data": {"period_return": 12}}
            return {}

        investor = InvestorAgent()
        investor.run_portfolio(
            str(POOL_FILE), save=False, research_fn=flaky_research)
        assert attempts[first[0]] == 2  # 重试后成功，未跳过

    def test_precalculated_scores_skip_research(self):
        """阶段间状态传递：分析阶段评分缓存直接复用，跳过采集分析"""
        from agents.investor import InvestorAgent

        def _must_not_be_called(symbol, name):
            raise AssertionError("scores 已提供时不应触发 research_fn")

        investor = InvestorAgent()
        portfolio = investor.run_portfolio(
            str(POOL_FILE), save=False,
            research_fn=_must_not_be_called,
            scores={"600519": 88.0, "000858": 80.0, "600887": 75.0})
        assert portfolio and sum(portfolio.values()) <= 1.0 + 1e-9
        assert set(portfolio) <= {"600519", "000858", "600887"}


class TestResearcherStages:
    """任务1/3：采集/分析两阶段拆分（缓存优先，缺数据不重采）"""

    def test_collect_only_uses_cache(self, tmp_path):
        from agents.researcher import ResearcherAgent
        agent = ResearcherAgent({"data_dir": str(tmp_path)})
        # 预置缓存（须含 market_cap，否则触发 H2 缺市值重采升级逻辑）
        (tmp_path / "600519_quote.json").write_text(
            json.dumps({"close": 1.0, "market_cap": 20000.0}),
            encoding="utf-8")
        (tmp_path / "600519_filing.json").write_text(
            json.dumps({"statements": []}), encoding="utf-8")
        (tmp_path / "600519_news.json").write_text(
            json.dumps({"items": []}), encoding="utf-8")
        plan = {"target": "600519", "name": "贵州茅台",
                "collect_tasks": ["quote", "filing", "news"]}
        data = agent.collect_only(plan)
        assert data["quote"] == {"close": 1.0, "market_cap": 20000.0}
        assert data["filing"] == {"statements": []}

    def test_analyze_cached_without_cache_is_empty_not_collect(self, tmp_path):
        """分析阶段缺缓存时返回空数据（不触发重采，由采集阶段兜底）"""
        from agents.researcher import ResearcherAgent
        agent = ResearcherAgent({"data_dir": str(tmp_path)})
        plan = {"target": "999999", "name": "无数据",
                "analyze_tasks": ["finance"]}
        result = agent.analyze_cached(plan)
        assert result["quote_data"] == {}
        assert result["filing_data"] == {}
        assert "citations" in result

    def test_score_pool_writes_cache(self, tmp_path, monkeypatch):
        """编排器分析阶段：评分缓存落盘 decision_log/scores_cache.json"""
        from orchestrator import ReportOrchestrator

        orch = ReportOrchestrator({"output_dir": str(tmp_path)})
        monkeypatch.setattr(
            orch.researcher, "analyze_cached",
            lambda plan: {"finance_analysis": None,
                          "quote_data": {"period_return": 20}})
        result = orch.score_pool(str(POOL_FILE), sector="消费板块", save=True)
        assert result["scores"]
        assert result["failed"] == {}
        cache = json.loads(Path(result["scores_cache"]).read_text(
            encoding="utf-8"))
        assert cache["scores"] == result["scores"]

    def test_collect_pool_retries_then_records(self, tmp_path, monkeypatch):
        """编排器采集阶段：失败重试一次后跳过留痕，不阻断批量"""
        from orchestrator import ReportOrchestrator
        from common.telemetry import RUN_STATS

        orch = ReportOrchestrator({"output_dir": str(tmp_path)})
        attempts = {}

        def flaky_collect(plan):
            symbol = plan["target"]
            attempts[symbol] = attempts.get(symbol, 0) + 1
            if symbol == "600519":
                raise RuntimeError("网络中断")
            return {"quote": {}}

        monkeypatch.setattr(orch.researcher, "collect_only", flaky_collect)
        before = len(RUN_STATS.failures)
        summary = orch.collect_pool(str(POOL_FILE), sector="消费板块")
        assert "600519" in summary["failed"]
        assert "600519" not in summary["ok"]
        assert summary["ok"]  # 其余标的采集不受阻
        assert attempts["600519"] == 2  # 重试一次
        assert any(f["where"] == "collect:600519"
                   for f in RUN_STATS.failures[before:])


class TestRunReportCli:
    """任务1/2：CLI 新子命令接线（argparse 层，不触发网络）"""

    def test_pool_subcommand_defaults_pool_file(self, monkeypatch):
        import run_report
        monkeypatch.setattr(
            run_report.sys, "argv", ["run_report.py", "pool"])
        args = run_report.parse_args()
        assert args.task == "pool"
        assert not hasattr(args, "pool_file")  # SUPPRESS：回退默认池

    def test_research_stage_required(self, monkeypatch):
        import run_report
        monkeypatch.setattr(
            run_report.sys, "argv",
            ["run_report.py", "research", "--stage", "collect",
             "--sector", "消费板块"])
        args = run_report.parse_args()
        assert args.stage == "collect"
        assert args.sector == "消费板块"

    def test_invest_skip_reports_and_cached_scores(self, monkeypatch):
        import run_report
        monkeypatch.setattr(
            run_report.sys, "argv",
            ["run_report.py", "invest", "--pool-file", "p.xlsx",
             "--skip-reports", "--use-cached-scores", "--save"])
        args = run_report.parse_args()
        assert args.skip_reports is True
        assert args.use_cached_scores is True


class TestIndustryMacroReports:
    """Day 6：行业/宏观研报端到端（真实编排+撰写+审查，研究数据 mock）"""

    def test_industry_report_end_to_end(self, tmp_path, monkeypatch):
        """行业研报：八章结构齐全、竞对表与柱状图同源、过审查闸门"""
        from orchestrator import ReportOrchestrator, ReportRequest
        from analyzers.industry_analyzer import IndustryAnalysis

        industry = IndustryAnalysis(
            sector="消费板块",
            prosperity={"news_count": 10, "positive_hits": 6,
                        "negative_hits": 1, "sentiment_score": 75,
                        "level": "景气向上", "policy_signals": ["促消费"]},
            competition={
                "companies": ["贵州茅台", "五粮液"],
                "table": [["指标", "贵州茅台", "五粮液"],
                          ["营业收入(亿元)", 1500.0, 800.0]],
                "target_rank": {"revenue": 1},
                "leader_metrics": ["营业收入(亿元)"]},
            peers=[("600519", "贵州茅台"), ("000858", "五粮液")],
            insights=["「消费板块」板块景气度判定为景气向上"
                      "（情绪分 75，近 10 条相关新闻，"
                      "正面信号 6 / 负面信号 1）"],
        )
        fake_data = {
            "quote_data": {}, "filing_data": {},
            "news_data": {"items": [{"title": "消费数据回暖",
                                     "source": "财联社",
                                     "date": "2026-08-01"}]},
            "knowledge_chunks": [], "claims": [],
            "industry_analysis": industry, "macro_analysis": None,
            "peer_metrics": {
                "600519": {"name": "贵州茅台", "net_profit": 800.0,
                           "revenue": 1500.0},
                "000858": {"name": "五粮液", "net_profit": 300.0,
                           "revenue": 800.0}},
            "report_type": "industry", "charts": [], "citations": [],
        }

        orch = ReportOrchestrator({
            "output_dir": str(tmp_path),
            "chart_dir": str(tmp_path / "charts"),
        })
        monkeypatch.setattr(
            orch.researcher, "research", lambda plan: fake_data)
        result = orch.generate(ReportRequest(
            report_type="industry", target="消费板块", name="消费板块"))

        assert "（待生成）" not in result.content  # 非占位骨架
        for section in ("板块核心观点", "投资结论与配置建议", "行业概况",
                        "景气度分析", "竞争格局与排名", "估值与资金面",
                        "风险提示", "数据来源"):
            assert section in result.content, f"缺失章节: {section}"
        # 竞对横向对比表与净利润数字注入正文（图文同源）
        assert "贵州茅台" in result.content
        assert "1500.0" in result.content
        assert "免责声明" in result.content
        assert result.passed_review, result.review_notes

    def test_macro_report_end_to_end(self, tmp_path, monkeypatch):
        """宏观研报：七章结构齐全、指标与期间同 MacroAnalyzer、过闸门"""
        from orchestrator import ReportOrchestrator, ReportRequest
        from analyzers.macro_analyzer import MacroAnalysis

        macro = MacroAnalysis(
            indicators={
                "GDP": {"value": 5.2, "period": "2026Q2",
                        "source": "国家统计局"},
                "CPI": {"value": 0.4, "period": "2026-07",
                        "source": "国家统计局"}},
            policy_trends={"货币政策": "稳健偏松"},
            sector_impact={"消费板块": "需求端温和修复，中性偏正面"},
            insights=["GDP 2026Q2 同比 5.2%，宏观延续温和修复"],
        )
        fake_data = {
            "quote_data": {}, "filing_data": {},
            "news_data": {"items": [{"title": "稳增长政策持续加码",
                                     "source": "证券时报",
                                     "date": "2026-07-30"}]},
            "knowledge_chunks": [], "claims": [],
            "industry_analysis": None, "macro_analysis": macro,
            "peer_metrics": {}, "report_type": "macro",
            "charts": [], "citations": [],
        }

        orch = ReportOrchestrator({"output_dir": str(tmp_path)})
        monkeypatch.setattr(
            orch.researcher, "research", lambda plan: fake_data)
        result = orch.generate(ReportRequest(
            report_type="macro", target="2026Q2", period="2026Q2"))

        assert "（待生成）" not in result.content
        for section in ("宏观核心观点", "宏观结论与板块配置建议",
                        "核心宏观指标", "政策动向", "对板块的影响分析",
                        "风险提示", "数据来源"):
            assert section in result.content, f"缺失章节: {section}"
        assert "国家统计局" in result.content  # 指标来源标注
        assert "免责声明" in result.content
        assert result.passed_review, result.review_notes

    def test_planner_industry_injects_pool(self):
        """Planner 行业分支注入公司池（板块级聚合依赖竞对名单）"""
        from agents.planner import PlannerAgent
        from types import SimpleNamespace

        plan = PlannerAgent({"pool_file": str(POOL_FILE)}).plan(
            SimpleNamespace(report_type="industry",
                            target="消费板块", name="消费板块"))
        assert plan["pool"], "industry 分支应注入公司池"
        assert plan["collect_tasks"] == ["news", "rag"]
        assert plan["analyze_tasks"] == ["industry", "macro"]
