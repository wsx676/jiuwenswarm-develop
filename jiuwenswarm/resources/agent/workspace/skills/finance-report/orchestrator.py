# -*- coding: utf-8 -*-
"""多 Agent 编排器（封装为 JiuwenSwarm 技能模块，可由 Swarmflow 工作流调度）

通过链式推理调度五个子 Agent，完成端到端投资决策与研报生成。
包含自检与反馈循环：Reviewer 不通过则回流 Researcher/Writer 重做；
报告定稿后由 Investor 完成选股评分与仓位配置。
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from agents.planner import PlannerAgent
from agents.researcher import ResearcherAgent
from agents.writer import WriterAgent
from agents.reviewer import ReviewerAgent
from agents.investor import InvestorAgent
from common.telemetry import RUN_STATS, fix_random_seed

logger = logging.getLogger(__name__)

# 成果可复现：进程启动即固定随机种子（全流程为确定性规则，
# 种子写入 run_stats.json，第三方按 README 可重放决策）
fix_random_seed()


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
        """端到端生成研报（各阶段耗时经遥测落盘，可复现可追溯）"""
        result = ReportResult(
            report_type=request.report_type, target=request.target
        )

        # 阶段1：任务规划（链式推理起点）
        with RUN_STATS.time_phase(f"规划:{request.target}"):
            plan = self.planner.plan(request)

        # 阶段2：数据研究
        with RUN_STATS.time_phase(f"研究:{request.target}"):
            research_data = self.researcher.research(plan)

        # 阶段3 + 4：撰写 + 审查（自检反馈循环：回流重写 ≤ 2 轮）
        with RUN_STATS.time_phase(f"撰写审查:{request.target}"):
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
            with RUN_STATS.time_phase(f"决策:{request.target}"):
                result.research_data = research_data
                result.portfolio = self.investor.decide(result)

        return result

    # ------------------------------------------------------------------
    # Swarmflow 五阶段工作流的分阶段入口（选股→采集→分析→决策→报告）
    # ------------------------------------------------------------------
    def _filtered_pool(self, pool_file: str, sector: str) -> dict:
        """加载公司池并按板块过滤（板块名非法时抛出供上层留痕）"""
        from collectors.pool_loader import load_pool
        pool = load_pool(pool_file)
        if sector:
            if sector not in pool:
                raise ValueError(
                    f"板块「{sector}」不在公司池内，可选: "
                    f"{'、'.join(pool.keys())}")
            pool = {sector: pool[sector]}
        return pool

    def collect_pool(self, pool_file: str, sector: str = "") -> dict:
        """「采集」阶段：按板块逐标的拉取数据（缓存优先，断点续采）

        Day 5 批量容错：单标的失败自动重试一次，仍失败跳过留痕，
        不阻断整体批量（采集产物落盘 data/，供「分析」阶段复用）。
        """
        pool = self._filtered_pool(pool_file, sector)
        ok, failed = [], {}
        with RUN_STATS.time_phase("采集"):
            for sector_name, items in pool.items():
                for symbol, name in items:
                    request = ReportRequest(
                        report_type="company", target=symbol, name=name)
                    plan = self.planner.plan(request)
                    data = None
                    for attempt in range(2):  # 有限重试：至多 2 次
                        try:
                            data = self.researcher.collect_only(plan)
                            break
                        except Exception as e:  # noqa: BLE001
                            if attempt == 0:
                                logger.warning(
                                    "采集 %s %s 失败，重试一次: %s",
                                    symbol, name, e)
                                continue
                            RUN_STATS.record_failure(
                                f"collect:{symbol}", e)
                    if data is not None:
                        ok.append(symbol)
                    else:
                        failed[symbol] = f"{sector_name}: 采集重试仍失败"
        return {"ok": ok, "failed": failed}

    def score_pool(self, pool_file: str, sector: str = "",
                   save: bool = False) -> dict:
        """「分析」阶段：读已采集数据跑分析引擎并因子打分

        确定性规则（不走 LLM、不重新采集）；评分缓存落盘
        decision_log/scores_cache.json，供「决策」阶段直接复用
        （阶段间状态传递）。
        """
        pool = self._filtered_pool(pool_file, sector)
        scores, failed = {}, {}
        with RUN_STATS.time_phase("分析"):
            for sector_name, items in pool.items():
                for symbol, name in items:
                    request = ReportRequest(
                        report_type="company", target=symbol, name=name)
                    plan = self.planner.plan(request)
                    try:
                        research_data = self.researcher.analyze_cached(plan)
                        scores[symbol] = self.investor.score_research(
                            research_data)
                    except Exception as e:  # noqa: BLE001
                        scores[symbol] = 0.0
                        failed[symbol] = str(e)[:200]
                        RUN_STATS.record_failure(f"analyze:{symbol}", e)

        result = {"scores": scores, "failed": failed}
        if save:
            log_dir = os.path.join(self.output_dir, "decision_log")
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(log_dir, "scores_cache.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            result["scores_cache"] = path
        return result

    def run_investment(self, pool_file: str, save: bool = False,
                       sector: str = "", reports: bool = True,
                       scores: Optional[dict] = None) -> dict:
        """公司池批量投资决策：逐标的采集分析评分 → 仓位配置

        支持单板块批量打通：sector 非空时只跑该板块（Day 4 验收：
        对某个板块跑批量流程产出多份报告 + 组合配置）。
        评分用因子规则（采集+分析即可，不走 LLM 研报生成，
        批量耗时可控；入选标的另由 decide 单标流程产出研报）。

        Args:
            reports: 入选标的是否补生成完整研报（Swarmflow「报告」
                阶段单独驱动时可置 False，决策阶段只出组合）。
            scores: 「分析」阶段预计算评分缓存；非空时跳过重复
                采集分析（阶段间状态传递）。
        """
        def _research(symbol: str, name: str) -> dict:
            request = ReportRequest(
                report_type="company", target=symbol, name=name)
            plan = self.planner.plan(request)
            return self.researcher.research(plan)

        with RUN_STATS.time_phase("决策"):
            portfolio = self.investor.run_portfolio(
                pool_file, save=save, output_dir=self.output_dir,
                research_fn=_research, sector=sector, scores=scores)

        # 入选标的产出完整研报（验收：批量流程产出多份报告 +
        # 组合配置；采集/分析已缓存，仅补 LLM 撰写开销）
        if save and reports and portfolio:
            with RUN_STATS.time_phase("报告"):
                from collectors.pool_loader import load_pool
                pool = load_pool(pool_file)
                name_map = {s: n for items in pool.values()
                            for s, n in items}
                for symbol in portfolio:
                    request = ReportRequest(
                        report_type="company", target=symbol,
                        name=name_map.get(symbol, ""))
                    try:
                        result = self.generate(request)
                        if result.content:
                            self.save_report(result, f"{symbol}.md")
                    except Exception as e:  # noqa: BLE001 单份研报失败不阻断
                        logger.warning("研报生成失败 %s: %s", symbol, e)
                        RUN_STATS.record_failure(f"report:{symbol}", e)
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
