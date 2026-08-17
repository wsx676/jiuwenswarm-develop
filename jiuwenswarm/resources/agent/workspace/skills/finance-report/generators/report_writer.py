# -*- coding: utf-8 -*-
"""结构化报告撰写器

按研报模板生成 Markdown，确保：
- 论点-论据链完整
- 所有数据标注来源
- 图表与正文同源
- 含"投资结论与仓位建议"章节（赛题要求）与风险提示、免责声明（合规）

长篇报告采用分治式生成：先 YAML 大纲后分段撰写，
用已生成内容反复喂回突破单次输出长度限制。
"""

from dataclasses import dataclass, field
from typing import List, Optional

try:
    from generators.chart_generator import Chart
    from analyzers.finance_analyzer import FinanceAnalysis
except ImportError:  # 兼容包导入/直跑：按绝对路径定位技能根目录
    import os
    import sys
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)
    from generators.chart_generator import Chart
    from analyzers.finance_analyzer import FinanceAnalysis


@dataclass
class ReportDraft:
    """报告初稿"""
    content: str = ""
    charts: List[Chart] = field(default_factory=list)
    claims: list = field(default_factory=list)   # 论据卡片（含引用）
    citations: List[str] = field(default_factory=list)


class ReportWriter:
    """报告撰写器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def write(
        self, research_data: dict, request
    ) -> ReportDraft:
        draft = ReportDraft()
        report_type = request.report_type

        if report_type == "company":
            draft = self._write_company(research_data, request)
        elif report_type == "industry":
            draft = self._write_industry(research_data, request)
        elif report_type == "macro":
            draft = self._write_macro(research_data, request)

        return draft

    def _write_company(
        self, data: dict, request
    ) -> ReportDraft:
        draft = ReportDraft()
        lines = []

        finance: FinanceAnalysis = data.get("finance_analysis")
        quote = data.get("quote_data", {})
        company_name = request.name or quote.get("name", "")

        # 标题与基本信息
        lines.append(f"# {company_name}（{request.target}）投资分析报告")
        lines.append("")
        lines.append("| 属性 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 报告类型 | 公司研报 |")
        lines.append(f"| 报告日期 | {request.period or '最新'} |")
        lines.append("")

        # 核心观点
        lines.append("## 一、核心观点")
        lines.append("")
        if finance and finance.insights:
            for insight in finance.insights:
                lines.append(f"- {insight}")
        lines.append("")

        # 投资结论与仓位建议（赛题要求的核心章节）
        lines.append("## 二、投资结论与仓位建议")
        lines.append("")
        lines.append(
            "（评级与仓位建议（0-10% 权重）及决策逻辑；"
            "空仓时阐明不配置理由）"
        )
        lines.append("")
        # TODO(Day 3): 由 LLM 基于研究数据生成分治式正文
        # （先 YAML 大纲后分段撰写，每条结论附数据支撑与来源）

        # 财务分析
        if finance:
            lines.append("## 四、财务分析")
            lines.append("")
            lines.append("### 4.1 盈利能力")
            lines.append("")
            lines.append("| 指标 | 数值 |")
            lines.append("|------|------|")
            for k, v in finance.profitability.items():
                lines.append(f"| {k} | {v} |")
            lines.append("")

        # 风险提示（合规必需）
        lines.append("## 六、风险提示")
        lines.append("")
        lines.append("- 宏观经济波动风险")
        lines.append("- 行业竞争加剧风险")
        lines.append("- 政策变化风险")
        lines.append("")

        # 数据来源与免责声明
        lines.append("---")
        lines.append("*数据来源：交易所信息披露平台、公开财经数据*")
        lines.append("*免责声明：本报告由 AI Agent 自动生成，仅供参考，不构成投资建议。*")

        draft.content = "\n".join(lines)
        draft.charts = data.get("charts", [])
        draft.citations = data.get("citations", [])

        return draft

    def _write_industry(self, data: dict, request) -> ReportDraft:
        """行业研报撰写"""
        # TODO(Day 3): 板块景气度 + 竞争格局（同板块竞对横向对比）
        return ReportDraft()

    def _write_macro(self, data: dict, request) -> ReportDraft:
        """宏观研报撰写"""
        # TODO(Day 3): 宏观指标 + 政策趋势 + 板块影响映射
        return ReportDraft()
