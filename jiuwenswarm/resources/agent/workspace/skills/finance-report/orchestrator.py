# -*- coding: utf-8 -*-
"""多 Agent 编排器（封装为 JiuwenSwarm 技能模块，可由 Swarmflow 工作流调度）

通过链式推理调度五个子 Agent，完成端到端投资决策与研报生成。
包含自检与反馈循环：Reviewer 不通过则回流 Researcher/Writer 重做；
报告定稿后由 Investor 完成选股评分与仓位配置。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from agents.planner import PlannerAgent
from agents.researcher import ResearcherAgent
from agents.writer import WriterAgent
from agents.reviewer import ReviewerAgent
from agents.investor import InvestorAgent


@dataclass
class ReportRequest:
    """研报生成请求"""
    report_type: str          # company / industry / macro
    target: str               # 股票代码 / 行业名 / 时间周期
    name: str = ""            # 公司名称 / 行业名称
    period: str = ""          # 报告周期
    max_revision_rounds: int = 2  # 最大修订轮次


@dataclass
class ReportResult:
    """研报生成结果"""
    report_type: str
    target: str
    content: str = ""
    charts: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    passed_review: bool = False
    review_notes: str = ""
    portfolio: dict = field(default_factory=dict)  # 投资决策结果（股票代码→仓位权重）
    research_data: dict = field(default_factory=dict)  # 供 Investor 因子打分


class ReportOrchestrator:
    """研报生成编排器"""

    # 报告按类型分目录存储（提交目录规范）
    REPORT_DIRS = {"company": "个股投资研报",
                   "industry": "行业研报",
                   "macro": "宏观研报"}

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.output_dir = self.config.get(
            "output_dir", os.path.join("reports", "finance-report")
        )
        self.planner = PlannerAgent(config)
        self.researcher = ResearcherAgent(config)
        self.writer = WriterAgent(config)
        self.reviewer = ReviewerAgent(config)
        self.investor = InvestorAgent(config)

    def generate(self, request: ReportRequest) -> ReportResult:
        """端到端生成研报"""
        result = ReportResult(
            report_type=request.report_type, target=request.target
        )

        # 阶段1：任务规划（链式推理起点）
        plan = self.planner.plan(request)

        # 阶段2：数据研究
        research_data = self.researcher.research(plan)

        # 阶段3 + 4：撰写 + 审查（自检反馈循环：回流重写 ≤ 2 轮）
        revision_feedback = None
        for round_idx in range(request.max_revision_rounds + 1):
            # 撰写报告（修订轮带上一轮审查反馈定向收敛）
            draft = self.writer.write(
                research_data, request,
                revision_feedback=revision_feedback)

            # 审查校验
            review = self.reviewer.review(draft, research_data)

            result.content = draft.content
            result.charts = draft.charts
            result.citations = draft.citations
            result.passed_review = review.passed
            result.review_notes = review.notes

            if review.passed:
                break

            # 未通过：定向补采数据缺口 + 问题清单回流 Writer 重写；
            # 2 轮未过则以当前稿放行并留痕（不阻断交付）
            if round_idx < request.max_revision_rounds:
                research_data = self.researcher.supplement(
                    research_data, review.feedback
                )
                revision_feedback = review.feedback
            else:
                result.review_notes += "；已达最大修订轮次，按当前稿放行"

        # 阶段5：投资决策（选股评分 + 仓位配置，输出 Portfolio.json）
        if request.report_type == "company":
            result.research_data = research_data
            result.portfolio = self.investor.decide(result)

        return result

    def run_investment(self, pool_file: str, save: bool = False,
                       sector: str = "") -> dict:
        """公司池批量投资决策：逐标的采集分析评分 → 仓位配置

        支持单板块批量打通：sector 非空时只跑该板块（Day 4 验收：
        对某个板块跑批量流程产出多份报告 + 组合配置）。
        评分用因子规则（采集+分析即可，不走 LLM 研报生成，
        批量耗时可控；入选标的另由 decide 单标流程产出研报）。
        """
        def _research(symbol: str, name: str) -> dict:
            request = ReportRequest(
                report_type="company", target=symbol, name=name)
            plan = self.planner.plan(request)
            return self.researcher.research(plan)

        portfolio = self.investor.run_portfolio(
            pool_file, save=save, output_dir=self.output_dir,
            research_fn=_research, sector=sector)

        # 入选标的产出完整研报（验收：批量流程产出多份报告 +
        # 组合配置；采集/分析已缓存，仅补 LLM 撰写开销）
        if save and portfolio:
            from collectors.pool_loader import load_pool
            pool = load_pool(pool_file)
            name_map = {s: n for items in pool.values()
                        for s, n in items}
            for symbol in portfolio:
                request = ReportRequest(
                    report_type="company", target=symbol,
                    name=name_map.get(symbol, ""))
                result = self.generate(request)
                if result.content:
                    self.save_report(result, f"{symbol}.md")
        return portfolio

    def save_report(self, result: ReportResult, filename: str) -> str:
        """保存研报为 Markdown（按报告类型分目录，个股按 股票代码.md 命名）"""
        report_dir = os.path.join(
            self.output_dir,
            self.REPORT_DIRS.get(result.report_type, "其他研报"))
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(report_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(result.content)
        return path

    def save_portfolio(self, portfolio: dict) -> str:
        """保存投资组合为 Portfolio.json（提交格式：{"股票代码": 持仓占比}）"""
        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, "Portfolio.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
        return path
