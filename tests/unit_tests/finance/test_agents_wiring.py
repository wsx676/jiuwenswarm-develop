# -*- coding: utf-8 -*-
"""编排/Agent 接线回归测试：
- M3：WriterAgent(config) → ReportWriter(config) 传参不再 TypeError
- L2：save_report 按报告类型分目录
- L6：仓位权重 round 累计误差由末位吸收，权重和恒 ≤ 1.0
"""

from types import SimpleNamespace

from agents.investor import InvestorAgent
from agents.writer import WriterAgent
from orchestrator import ReportOrchestrator, ReportResult


class TestWriterWiring:
    def test_writer_agent_passes_config_to_report_writer(self, monkeypatch):
        """M3 回归：company 路径端到端可跑通（此前 ReportWriter 不接受
        config 参数，一跑即 TypeError）；离线跑规则降级路径，不依赖 LLM"""
        import generators.report_writer as rw
        monkeypatch.setattr(
            rw.ReportWriter, "_get_llm", lambda self: None)
        request = SimpleNamespace(
            report_type="company", target="600519",
            name="贵州茅台", period="2026-Q2")
        draft = WriterAgent({"llm": {}}).write(
            {"finance_analysis": None,
             "quote_data": {"name": "贵州茅台"},
             "charts": [], "citations": []},
            request)
        assert "贵州茅台" in draft.content
        assert draft.content.startswith("# ")


class TestSaveReportDirs:
    def test_save_report_by_type(self, tmp_path):
        """L2 回归：行业报告入 行业研报/，公司报告入 个股投资研报/"""
        orch = ReportOrchestrator({"output_dir": str(tmp_path)})
        p1 = orch.save_report(
            ReportResult(report_type="industry", target="白酒", content="x"),
            "白酒行业.md")
        p2 = orch.save_report(
            ReportResult(report_type="company", target="600519", content="y"),
            "600519.md")
        assert "行业研报" in p1
        assert "个股投资研报" in p2


class TestAllocateWeights:
    def test_weights_sum_never_exceeds_one(self):
        """L6 回归：round(0.34)+round(0.34)+round(0.33)=1.01 类误差
        由末位吸收，总权重不超 1.0（提交硬约束）"""
        agent = InvestorAgent()
        scores = {"600519": 100.0, "000858": 97.0, "000568": 97.0}
        portfolio = agent._allocate(scores, set(scores))
        assert len(portfolio) == 3
        assert sum(portfolio.values()) <= 1.0 + 1e-9

    def test_allocate_empty_when_all_below_threshold(self):
        """低于阈值全部剔除：空仓决策（应阐明理由，由决策日志记录）"""
        agent = InvestorAgent()
        portfolio = agent._allocate(
            {"600519": 50.0}, {"600519"})
        assert portfolio == {}


class TestPlannerWiring:
    def test_plan_extracts_sector_and_competitors(self):
        """公司池加载：板块归属 + 同板块竞对名单（白名单校验）"""
        import os
        from agents.planner import PlannerAgent, DEFAULT_POOL_FILE
        assert os.path.exists(DEFAULT_POOL_FILE)   # 组委会公司池就位
        plan = PlannerAgent({}).plan(SimpleNamespace(
            report_type="company", target="600519", name="贵州茅台"))
        assert plan["sector"] == "消费板块"
        assert "000858" in plan["competitors"]    # 五粮液同板块
        assert "600809" in plan["competitors"]    # 山西汾酒同板块

    def test_plan_invalid_symbol_degrades(self):
        """白名单外标的：无板块无竞对，不阻断（降级告警）"""
        from agents.planner import PlannerAgent
        plan = PlannerAgent({}).plan(SimpleNamespace(
            report_type="company", target="999999", name="X"))
        assert plan["sector"] == ""
        assert plan["competitors"] == []


class TestReviewerCitationGate:
    def test_low_citation_rate_fails_review(self):
        """引用率闸门：数据句缺来源标注 → 审查发现引用率问题"""
        from agents.reviewer import ReviewerAgent
        draft = SimpleNamespace(
            content=("## 一、核心观点\n\n营收 500 亿元创新高。\n\n"
                     "## 七、风险提示\n\n需求波动。\n\n免责声明：x"),
            claims=[], charts=[],
        )
        review = ReviewerAgent().review(draft, {})
        assert any("引用率" in i for i in review.issues)

    def test_reviewer_claims_dict_accepted(self):
        """claims 为 {text, citation} 字典（此前 getattr(dict) 全误报）"""
        from agents.reviewer import ReviewerAgent
        draft = SimpleNamespace(
            content=("## 一、核心观点\n\n## 二、投资结论与仓位建议\n\n"
                     "## 五、财务分析\n\n数据来源：公司定期财报\n\n"
                     "## 六、估值分析\n\n## 七、风险提示\n\n免责声明：x"),
            claims=[{"text": "毛利率 89.6%",
                     "citation": "公司定期财报"}],
            charts=[],
        )
        review = ReviewerAgent().review(draft, {})
        assert review.issues == []
        assert review.passed
