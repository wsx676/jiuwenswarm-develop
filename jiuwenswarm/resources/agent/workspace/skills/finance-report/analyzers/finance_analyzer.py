# -*- coding: utf-8 -*-
"""财务分析器

对财务三表进行结构化分析：
- 盈利能力：毛利率、净利率、ROE、ROA
- 偿债能力：资产负债率、流动比率
- 营运能力：存货周转、应收周转
- 成长能力：营收增速、净利润增速
- 估值：PE、PB、PS
"""

from dataclasses import dataclass, field
from typing import List

from ..collectors.filing_collector import FinancialStatement


@dataclass
class FinanceAnalysis:
    """财务分析结果"""
    profitability: dict = field(default_factory=dict)  # 盈利能力
    solvency: dict = field(default_factory=dict)       # 偿债能力
    operation: dict = field(default_factory=dict)      # 营运能力
    growth: dict = field(default_factory=dict)         # 成长能力
    valuation: dict = field(default_factory=dict)      # 估值
    insights: List[str] = field(default_factory=list)  # 分析洞察

    def to_dict(self) -> dict:
        return {
            "profitability": self.profitability,
            "solvency": self.solvency,
            "operation": self.operation,
            "growth": self.growth,
            "valuation": self.valuation,
            "insights": self.insights,
        }


class FinanceAnalyzer:
    """财务分析器"""

    def analyze(
        self,
        statements: List[FinancialStatement],
        quote_data: dict = None,
    ) -> FinanceAnalysis:
        result = FinanceAnalysis()

        if not statements:
            result.insights.append("暂无公开财务数据")
            return result

        latest = statements[-1]

        # 盈利能力
        result.profitability = {
            "gross_margin": latest.gross_margin,
            "net_margin": latest.net_margin,
            "roe": latest.roe,
        }

        # 偿债能力
        result.solvency = {
            "debt_ratio": latest.debt_ratio,
        }

        # 成长能力（同比）
        if len(statements) >= 2:
            prev = statements[-2]
            if prev.revenue > 0:
                rev_growth = (latest.revenue / prev.revenue - 1) * 100
                result.growth["revenue_growth"] = round(rev_growth, 2)
            if prev.net_profit > 0:
                profit_growth = (
                    latest.net_profit / prev.net_profit - 1
                ) * 100
                result.growth["net_profit_growth"] = round(
                    profit_growth, 2
                )

        # 估值（需结合行情市值数据）
        if quote_data and latest.net_profit > 0:
            market_cap = quote_data.get("market_cap", 0)
            if market_cap > 0:
                result.valuation["pe"] = round(
                    market_cap / latest.net_profit, 2
                )

        # 生成分析洞察
        result.insights = self._generate_insights(result, statements)

        return result

    def _generate_insights(
        self, result: FinanceAnalysis,
        statements: List[FinancialStatement],
    ) -> List[str]:
        insights = []

        gm = result.profitability.get("gross_margin", 0)
        if gm > 0.5:
            insights.append(f"毛利率 {gm:.1%}，处于较高水平，议价能力强")
        elif gm < 0.2:
            insights.append(f"毛利率 {gm:.1%}，偏低，需关注成本控制")

        rev_g = result.growth.get("revenue_growth")
        if rev_g is not None:
            if rev_g > 20:
                insights.append(f"营收增速 {rev_g:.1f}%，成长性突出")
            elif rev_g < 0:
                insights.append(f"营收增速 {rev_g:.1f}%，出现下滑，需警惕")

        return insights
