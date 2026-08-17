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
    def test_writer_agent_passes_config_to_report_writer(self):
        """M3 回归：company 路径端到端可跑通（此前 ReportWriter 不接受
        config 参数，一跑即 TypeError）"""
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
