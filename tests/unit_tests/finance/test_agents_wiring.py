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
        assert any("空仓" in n for n in agent.decision_notes)

    def test_min_position_count_note_recorded(self):
        """M2 回归：达标标的不足 min_position_count 时留痕阐明
        （此前配置读取后从未生效）"""
        agent = InvestorAgent()
        scores = {"600519": 90.0, "000858": 80.0}   # 仅 2 只 < 3
        portfolio = agent._allocate(scores, set(scores))
        assert len(portfolio) == 2                   # 软约束：仍配置
        assert any("分散度" in n for n in agent.decision_notes)

    def test_save_writes_decision_log(self, tmp_path):
        """M2 回归：决策日志随 Portfolio.json 落盘（风控可追溯）"""
        import json
        import os
        agent = InvestorAgent()
        scores = {"600519": 90.0}
        portfolio = agent._allocate(scores, set(scores))
        agent._save(portfolio, str(tmp_path), scores,
                    agent.decision_notes)
        log = json.load(open(
            os.path.join(str(tmp_path), "decision_log", "decision.json"),
            encoding="utf-8"))
        assert log["portfolio"] == portfolio
        assert any("分散度" in n for n in log["notes"])


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
                     "评级建议：增持。\n\n"
                     "## 五、财务分析\n\n数据来源：公司定期财报\n\n"
                     "## 六、估值分析\n\n## 七、风险提示\n\n免责声明：x"),
            claims=[{"text": "毛利率 89.6%",
                     "citation": "公司定期财报"}],
            charts=[],
        )
        review = ReviewerAgent().review(draft, {})
        assert review.issues == []
        assert review.passed


class TestChartTextConsistency:
    """M1 回归：图文一致性检查实装（此前读 Chart 不存在字段，
    恒通过的死代码）"""

    @staticmethod
    def _sections():
        return ("## 一、核心观点\n\n## 二、投资结论与仓位建议\n\n"
                "## 五、财务分析\n\n## 六、估值分析\n\n"
                "## 七、风险提示\n\n免责声明：x\n\n数据来源：公开行情")

    def test_chart_value_missing_in_body_flagged(self):
        from agents.reviewer import ReviewerAgent
        chart = SimpleNamespace(
            chart_type="line", title="股价走势",
            data={"latest_close": 1286.09})
        draft = SimpleNamespace(
            content=self._sections(), claims=[], charts=[chart])
        review = ReviewerAgent().review(draft, {})
        assert any("图文不一致" in i for i in review.issues)

    def test_chart_value_present_in_body_passes(self):
        from agents.reviewer import ReviewerAgent
        chart = SimpleNamespace(
            chart_type="line", title="股价走势",
            data={"latest_close": 1286.09})
        draft = SimpleNamespace(
            content=self._sections() + "\n最新收盘 1286.09 元。",
            claims=[], charts=[chart])
        review = ReviewerAgent().review(draft, {})
        assert not any("图文不一致" in i for i in review.issues)


class TestRunReportArgparse:
    def test_output_dir_after_subcommand(self, monkeypatch):
        """L3 回归：--output-dir 放子命令后不再报 unrecognized arguments"""
        import run_report
        monkeypatch.setattr(
            run_report.sys, "argv",
            ["run_report.py", "company", "--target", "600519",
             "--output-dir", "out_dir"])
        args = run_report.parse_args()
        assert args.output_dir == "out_dir"
        assert args.task == "company"

    def test_output_dir_before_subcommand_not_overridden(self, monkeypatch):
        """L3 回归：旧用法（主 parser 前缀传参）不被子 parser
        默认值覆盖（此前 default 双写致绝对路径被重置为相对默认目录，
        报告落盘到技能根下而非指定目录）"""
        import run_report
        monkeypatch.setattr(
            run_report.sys, "argv",
            ["run_report.py", "--output-dir", "abs_out",
             "company", "--target", "600519"])
        args = run_report.parse_args()
        assert args.output_dir == "abs_out"

    def test_output_dir_default_fallback(self, monkeypatch):
        """两层均未显式传参：main 层 getattr 回退默认目录"""
        import run_report
        monkeypatch.setattr(
            run_report.sys, "argv",
            ["run_report.py", "company", "--target", "600519"])
        args = run_report.parse_args()
        assert not hasattr(args, "output_dir")   # SUPPRESS 不写 namespace
        assert getattr(args, "output_dir",
                       run_report.DEFAULT_OUTPUT_DIR) \
            == run_report.DEFAULT_OUTPUT_DIR


class TestRevisionFeedbackLoop:
    """Day 4 回归：Reviewer 不通过 → 问题清单回流 Writer 重写，
    ≤2 轮收敛（此前 orchestrator 重写轮不带反馈，盲写难收敛）"""

    def test_revision_instructions_mapping(self):
        import generators.report_writer as rw
        out = rw.ReportWriter._revision_instructions([
            "正文数据句引用率 80% 低于 90% 闸门（2 处缺来源标注）",
            "图文不一致: 股价走势的 latest_close=1286.09 未在正文出现",
            "正文数据句引用率 80% 低于 90% 闸门（2 处缺来源标注）",
        ])
        assert "数据来源" in out
        assert "图文不一致" in out
        assert out.count("引用率须≥90%") == 1   # 同类问题去重

    def test_no_issues_no_instructions(self):
        import generators.report_writer as rw
        assert rw.ReportWriter._revision_instructions([]) == ""

    def test_writer_receives_revision_feedback(self, monkeypatch):
        """WriterAgent.write 透传 revision_feedback 到 ReportWriter"""
        import generators.report_writer as rw
        from agents.writer import WriterAgent
        seen = {}

        def fake_write(self, data, request, revision_feedback=None):
            seen["feedback"] = revision_feedback
            return rw.ReportDraft(content="# x")

        monkeypatch.setattr(rw.ReportWriter, "write", fake_write)
        feedback = {"issues": ["缺失免责声明"], "research_data": {}}
        WriterAgent({}).write({}, SimpleNamespace(
            report_type="company", target="600519"),
            revision_feedback=feedback)
        assert seen["feedback"] == feedback

    def test_orchestrator_loop_converges_with_feedback(self, monkeypatch):
        """首轮不过→带反馈重写→第二轮通过（反馈回流端到端）"""
        from orchestrator import ReportOrchestrator, ReportRequest
        orch = ReportOrchestrator({})
        monkeypatch.setattr(
            orch.planner, "plan", lambda req: {"report_type": "company",
                                               "target": "600519"})
        monkeypatch.setattr(
            orch.researcher, "research", lambda plan: {})
        monkeypatch.setattr(
            orch.researcher, "supplement",
            lambda data, fb: dict(data, supplemented=True))

        drafts = []

        def fake_write(data, request, revision_feedback=None):
            drafts.append(revision_feedback)
            return SimpleNamespace(
                content="x", charts=[], citations=[])
        monkeypatch.setattr(orch.writer, "write", fake_write)

        reviews = [False, True]
        monkeypatch.setattr(
            orch.reviewer, "review",
            lambda draft, data: SimpleNamespace(
                passed=reviews.pop(0), score=80.0,
                notes="n", issues=["正文数据句引用率 80% 低于闸门"],
                feedback={"issues": ["引用率低"], "research_data": {}}))

        result = orch.generate(ReportRequest(
            report_type="company", target="600519", name="贵州茅台"))
        assert result.passed_review
        assert drafts[0] is None                # 首轮无反馈
        assert drafts[1] == {"issues": ["引用率低"],
                             "research_data": {}}   # 修订轮回流

    def test_max_rounds_releases_current_draft(self, monkeypatch):
        """2 轮未过：按当前稿放行并留痕（不阻断交付）"""
        from orchestrator import ReportOrchestrator, ReportRequest
        orch = ReportOrchestrator({})
        monkeypatch.setattr(
            orch.planner, "plan", lambda req: {"report_type": "company",
                                               "target": "600519"})
        monkeypatch.setattr(
            orch.researcher, "research", lambda plan: {})
        monkeypatch.setattr(
            orch.researcher, "supplement", lambda d, fb: d)
        monkeypatch.setattr(
            orch.writer, "write",
            lambda data, req, revision_feedback=None: SimpleNamespace(
                content="x", charts=[], citations=[]))
        monkeypatch.setattr(
            orch.reviewer, "review",
            lambda draft, data: SimpleNamespace(
                passed=False, score=50.0, notes="未通过",
                issues=["x"], feedback={"issues": ["x"]}))
        result = orch.generate(ReportRequest(
            report_type="company", target="600519", name="贵州茅台",
            max_revision_rounds=1))
        assert not result.passed_review
        assert "最大修订轮次" in result.review_notes
        assert result.content == "x"            # 当前稿放行


class TestDegradedReportPassesReviewer:
    def test_offline_degraded_draft_passes_review(self, monkeypatch):
        """H1 回归：无 LLM 全降级报告也必须通过自研 Reviewer 闸门
        （一/二/七章模板段此前缺来源标注，引用率仅 30% 空转三轮）"""
        import generators.report_writer as rw
        from agents.reviewer import ReviewerAgent
        monkeypatch.setattr(
            rw.ReportWriter, "_get_llm", lambda self: None)
        request = SimpleNamespace(
            report_type="company", target="600519",
            name="贵州茅台", period="2026-Q2")
        draft = WriterAgent({"llm": {}}).write(
            {"finance_analysis": None,
             "quote_data": {"name": "贵州茅台", "source": "mock行情源",
                            "collected_at": "2026-08-17T10:00:00",
                            "latest_close": 1400.0,
                            "period_return": 5.2},
             "charts": [], "citations": []},
            request)
        review = ReviewerAgent().review(draft, {})
        assert review.issues == [], review.issues
        assert review.passed
        assert review.score >= 90.0
