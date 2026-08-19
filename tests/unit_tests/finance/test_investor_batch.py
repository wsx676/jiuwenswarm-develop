# -*- coding: utf-8 -*-
"""InvestorAgent 因子打分 / Portfolio 校验 / 批量板块打通回归（Day 4 任务 4-6）

全离线：不触发采集与网络；批量流程用注入的 research_fn 与
monkeypatch 的编排组件。
"""

from types import SimpleNamespace

import pytest


def _strong_research():
    """构造高分因子样本（茅台型：高 ROE/低负债/正增长/合理 PE）"""
    finance = SimpleNamespace(
        profitability={"roe": 28.0, "gross_margin": 90.0},
        solvency={"debt_ratio": 20.0, "cashflow_to_profit": 1.2},
        growth={"revenue_growth": 16.0, "net_profit_growth": 18.0},
        valuation={"pe": 22.0},
    )
    return {
        "finance_analysis": finance,
        "quote_data": {"period_return": 12.0, "latest_close": 1500.0},
    }


def _weak_research():
    """构造全负样本（验收：空仓场景须阐明决策逻辑）"""
    finance = SimpleNamespace(
        profitability={"roe": -3.0, "gross_margin": 10.0},
        solvency={"debt_ratio": 85.0},
        growth={"revenue_growth": -20.0, "net_profit_growth": -30.0},
        valuation={"pe": -12.0},
    )
    return {
        "finance_analysis": finance,
        "quote_data": {"period_return": -25.0},
        "risk_signals": ["业绩预亏", "立案调查"],
    }


class TestFactorScoring:
    def test_strong_sample_high_score(self):
        from agents.investor import InvestorAgent
        score = InvestorAgent({}).score_research(_strong_research())
        assert score >= 90.0

    def test_all_negative_sample_low_score(self):
        from agents.investor import InvestorAgent
        score = InvestorAgent({}).score_research(_weak_research())
        assert score < 60.0          # 低于默认阈值，不入选

    def test_deterministic(self):
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        assert inv.score_research(_strong_research()) == \
            inv.score_research(_strong_research())

    def test_empty_research_zero(self):
        from agents.investor import InvestorAgent
        assert InvestorAgent({}).score_research({}) == 0.0

    def test_score_report_gates_on_review(self):
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        failed = SimpleNamespace(passed_review=False,
                                 research_data=_strong_research())
        assert inv.score_report(failed) == 0.0   # 审查未过不配置
        passed = SimpleNamespace(passed_review=True,
                                 research_data=_strong_research())
        assert inv.score_report(passed) >= 90.0


class TestValidatePortfolio:
    def test_valid_portfolio(self):
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        allowed = {"600519", "000858", "600809"}
        assert inv.validate_portfolio(
            {"600519": 0.4, "000858": 0.35, "600809": 0.25}, allowed) == []
        assert inv.validate_portfolio({}, allowed) == []   # 空仓合法

    def test_out_of_whitelist(self):
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        errors = inv.validate_portfolio({"999999": 0.5}, {"600519"})
        assert any("白名单" in e for e in errors)

    def test_weight_over_limit(self):
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        errors = inv.validate_portfolio({"600519": 0.5}, {"600519"})
        assert any("0.4" in e for e in errors)

    def test_total_over_one(self):
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        errors = inv.validate_portfolio(
            {"600519": 0.4, "000858": 0.4, "600809": 0.4},
            {"600519", "000858", "600809"})
        assert any("总权重" in e for e in errors)


class TestBatchPortfolio:
    @pytest.fixture
    def fake_pool(self, monkeypatch):
        pool = {
            "消费": [("600519", "贵州茅台"), ("000858", "五粮液"),
                     ("600809", "山西汾酒")],
            "医药": [("600276", "恒瑞医药")],
        }
        monkeypatch.setattr(
            "collectors.pool_loader.load_pool", lambda f: pool)
        monkeypatch.setattr(
            "collectors.pool_loader.whitelist_symbols",
            lambda p: {s for items in p.values() for s, _ in items})
        return pool

    def test_sector_filter_batch(self, fake_pool):
        from agents.investor import InvestorAgent
        calls = []

        def research_fn(symbol, name):
            calls.append(symbol)
            return _strong_research() if symbol == "600519" else _weak_research()

        inv = InvestorAgent({})
        portfolio = inv.run_portfolio(
            "pool.xlsx", research_fn=research_fn, sector="消费")
        assert calls == ["600519", "000858", "600809"]   # 只跑该板块
        assert "600519" in portfolio
        assert "600276" not in portfolio

    def test_unknown_sector_raises(self, fake_pool):
        from agents.investor import InvestorAgent
        with pytest.raises(ValueError, match="不在公司池内"):
            InvestorAgent({}).run_portfolio(
                "pool.xlsx", research_fn=lambda s, n: {}, sector="航天")

    def test_single_failure_not_blocking(self, fake_pool):
        from agents.investor import InvestorAgent

        def research_fn(symbol, name):
            if symbol == "000858":
                raise RuntimeError("采集失败")
            return _strong_research()

        portfolio = InvestorAgent({}).run_portfolio(
            "pool.xlsx", research_fn=research_fn, sector="消费")
        assert "000858" not in portfolio
        assert set(portfolio) == {"600519", "600809"}

    def test_empty_position_with_decision_log(self, fake_pool, tmp_path):
        """验收：全负样本空仓，Portfolio.json 为 {} 且决策日志阐明逻辑"""
        import json
        import os
        from agents.investor import InvestorAgent

        inv = InvestorAgent({})
        portfolio = inv.run_portfolio(
            "pool.xlsx", save=True, output_dir=str(tmp_path),
            research_fn=lambda s, n: _weak_research(), sector="消费")
        assert portfolio == {}
        log = json.load(open(
            tmp_path / "decision_log" / "decision.json", encoding="utf-8"))
        assert log["empty_position"] is True
        assert log["empty_reason"]                # 空仓理由留痕
        assert any("空仓" in n for n in log["notes"])

    def test_batch_save_writes_valid_portfolio(self, fake_pool, tmp_path):
        import json
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        portfolio = inv.run_portfolio(
            "pool.xlsx", save=True, output_dir=str(tmp_path),
            research_fn=lambda s, n: _strong_research(), sector="消费")
        saved = json.load(open(tmp_path / "Portfolio.json", encoding="utf-8"))
        assert saved == portfolio
        assert sum(saved.values()) <= 1.0 + 1e-9
        assert all(0 < w <= 0.4 + 1e-9 for w in saved.values())
        # L4：决策日志须含时间戳（复现批次对齐）
        log = json.load(open(
            tmp_path / "decision_log" / "decision.json", encoding="utf-8"))
        assert log.get("generated_at")
        # 3.2：仓位决策与理由须阐明（赛题要求：满仓/半仓/空仓均须说明逻辑）
        assert log.get("position_decision") in ("full", "partial", "empty")
        assert log.get("position_rationale")
        if log["empty_position"]:
            assert log["position_decision"] == "empty"
        elif sum(saved.values()) < 0.95:
            assert log["position_decision"] == "partial"
            assert "现金" in log["position_rationale"]  # 半仓阐明现金保留

    def test_cached_scores_respect_sector(self, fake_pool, tmp_path):
        """M1 回归：--use-cached-scores + --sector 组合时，
        全池缓存评分须按板块收窄过滤，非目标板块标的不入选"""
        import json
        from agents.investor import InvestorAgent

        def must_not_be_called(symbol, name):
            raise AssertionError("缓存评分路径不应触发实时采集")

        scores = {"600519": 95.0, "000858": 90.0,
                  "600276": 98.0}  # 600276 属「医药」板块
        inv = InvestorAgent({})
        portfolio = inv.run_portfolio(
            "pool.xlsx", save=True, output_dir=str(tmp_path),
            research_fn=must_not_be_called, sector="消费", scores=scores)
        assert set(portfolio) == {"600519", "000858"}  # 600276 被过滤
        log = json.load(open(
            tmp_path / "decision_log" / "decision.json", encoding="utf-8"))
        assert "600276" not in log["scores"]   # 决策留痕同口径

    def test_rounding_never_exceeds_total(self):
        """8 只近似均分：round 逐项舍入累计误差不得使总权重超 1.0
        （消费板块实测 6×0.13+0.12+0.10 场景；提交硬约束）"""
        from agents.investor import InvestorAgent
        scores = dict(zip(
            ["600519", "000858", "600809", "600887", "603288",
             "601888", "600660", "000333"],
            [72.0, 73.0, 62.0, 69.0, 69.0, 69.0, 66.0, 71.0]))
        inv = InvestorAgent({})
        portfolio = inv._allocate(scores, set(scores))
        assert sum(portfolio.values()) <= 1.0 + 1e-9
        assert all(w <= inv.max_weight for w in portfolio.values())
        assert inv.validate_portfolio(portfolio, set(scores)) == []


class TestOrchestratorInvestWiring:
    def test_generate_result_carries_research_data(self, monkeypatch):
        """orchestrator 须把 research_data 挂到 result 供打分"""
        from orchestrator import ReportOrchestrator, ReportRequest
        orch = ReportOrchestrator({})
        monkeypatch.setattr(
            orch.planner, "plan", lambda req: {"report_type": "company",
                                               "target": "600519"})
        monkeypatch.setattr(
            orch.researcher, "research", lambda plan: {"quote_data": {}})
        monkeypatch.setattr(
            orch.writer, "write",
            lambda data, req, revision_feedback=None: SimpleNamespace(
                content="x", charts=[], citations=[]))
        monkeypatch.setattr(
            orch.reviewer, "review",
            lambda draft, data: SimpleNamespace(
                passed=True, score=100.0, notes="", issues=[],
                feedback={}))
        monkeypatch.setattr(orch.investor, "decide", lambda r: {})
        result = orch.generate(ReportRequest(
            report_type="company", target="600519", name="贵州茅台"))
        assert result.research_data == {"quote_data": {}}

    def test_run_investment_generates_reports_for_selected(
            self, monkeypatch, tmp_path):
        """验收：批量流程产出多份报告 + 组合配置"""
        from orchestrator import ReportOrchestrator
        orch = ReportOrchestrator({"output_dir": str(tmp_path)})
        pool = {"消费": [("600519", "贵州茅台"), ("000858", "五粮液")]}
        monkeypatch.setattr(
            "collectors.pool_loader.load_pool", lambda f: pool)
        monkeypatch.setattr(
            "collectors.pool_loader.whitelist_symbols",
            lambda p: {s for items in p.values() for s, _ in items})
        monkeypatch.setattr(
            orch.planner, "plan", lambda req: {"report_type": "company",
                                               "target": req.target})
        monkeypatch.setattr(
            orch.researcher, "research", lambda plan: _strong_research())
        # 入选标的研报生成：打桩 writer/reviewer（不走真实撰写）
        monkeypatch.setattr(
            orch.writer, "write",
            lambda data, req, revision_feedback=None: SimpleNamespace(
                content=f"# {req.target}研报\n核心观点 投资结论 财务分析 "
                        "估值分析 风险提示 免责声明 数据来源",
                charts=[], citations=[]))
        monkeypatch.setattr(
            orch.reviewer, "review",
            lambda draft, data: SimpleNamespace(
                passed=True, score=100.0, notes="", issues=[],
                feedback={}))
        saved_files = []
        monkeypatch.setattr(
            orch, "save_report",
            lambda result, filename: saved_files.append(filename))
        portfolio = orch.run_investment("pool.xlsx", save=True, sector="消费")
        assert portfolio
        # 入选标的各产出一份研报
        assert sorted(saved_files) == sorted(f"{s}.md" for s in portfolio)


class TestPositionStance:
    """3.2：仓位决策与理由（满仓/半仓/空仓均须阐明决策逻辑）"""

    def _inv(self):
        from agents.investor import InvestorAgent
        return InvestorAgent({})

    def test_empty_position_rationale(self):
        decision, rationale = self._inv()._position_stance(
            {}, {"600519": 40.0, "000858": 30.0})
        assert decision == "empty"
        assert "阈值" in rationale and "空仓" in rationale

    def test_partial_position_rationale(self):
        scores = {"600519": 80.0, "000858": 70.0, "600276": 40.0}
        decision, rationale = self._inv()._position_stance(
            {"600519": 0.45, "000858": 0.35}, scores)
        assert decision == "partial"
        assert "平均评分 75.0" in rationale   # 入选均分真实计算
        assert "1 只未达" in rationale          # 未达标标的数
        assert "现金" in rationale              # 现金保留阐明

    def test_full_position_rationale(self):
        scores = {"600519": 80.0, "000858": 70.0}
        decision, rationale = self._inv()._position_stance(
            {"600519": 0.55, "000858": 0.45}, scores)
        assert decision == "full"
        assert "满仓" in rationale
