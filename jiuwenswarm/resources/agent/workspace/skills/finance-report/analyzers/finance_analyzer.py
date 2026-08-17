# -*- coding: utf-8 -*-
"""财务分析器

对财务三表进行结构化分析（指标取披露口径，非自行计算）：
- 盈利能力：毛利率、净利率、ROE、ROA
- 偿债能力：资产负债率、经营现金流覆盖
- 成长能力：营收增速、净利润增速（同比：与上年同期报告期对比）
- 估值：PE（年化净利润口径，支持市值或「最新价×总股本」两种输入）
- 洞察生成：规则化触发，确保结论有据可依（报告溯源基础）
"""

from dataclasses import dataclass, field
from typing import List, Optional

try:
    from collectors.filing_collector import FinancialStatement
except ImportError:  # 兼容包导入/直跑：按绝对路径定位技能根目录
    import os
    import sys
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)
    from collectors.filing_collector import FinancialStatement


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
        quote_data: Optional[dict] = None,
    ) -> FinanceAnalysis:
        """分析财报序列（自动按报告期升序；同比取上年同期报告期）

        quote_data 可选字段：market_cap（总市值，亿元）或
        latest_close（最新价，元）+ total_shares（总股本，亿股）；
        净利润均按采集披露口径（元）。
        """
        result = FinanceAnalysis()

        if not statements:
            result.insights.append("暂无公开财务数据")
            return result

        # 落盘数据可能为降序，统一按报告期升序（末位为最新期）
        statements = sorted(statements, key=lambda s: self._period_key(
            s.period))
        latest = statements[-1]

        # 盈利能力（披露口径；缺失指标为 None 时跳过，不写 0.0 哨兵）
        prof = {}
        if latest.gross_margin is not None:
            prof["gross_margin"] = latest.gross_margin
        if latest.net_margin is not None:
            prof["net_margin"] = latest.net_margin
        if latest.roe is not None:
            prof["roe"] = latest.roe
        if latest.total_assets and latest.net_profit is not None:
            prof["roa"] = round(
                latest.net_profit / latest.total_assets * 100, 2)
        result.profitability = prof

        # 偿债能力
        solvency = {}
        if latest.debt_ratio is not None:
            solvency["debt_ratio"] = latest.debt_ratio
        if (latest.net_profit and latest.net_profit > 0
                and latest.operating_cashflow is not None):
            solvency["cashflow_to_profit"] = round(
                latest.operating_cashflow / latest.net_profit, 2)
        result.solvency = solvency

        # 成长能力（同比：与上年同期报告期对比，而非上一期）
        prev = self._yoy_prev(latest, statements)
        if prev is not None:
            if prev.revenue and latest.revenue is not None:
                result.growth["revenue_growth"] = round(
                    (latest.revenue / prev.revenue - 1) * 100, 2)
            if prev.net_profit and latest.net_profit is not None:
                result.growth["net_profit_growth"] = round(
                    (latest.net_profit / prev.net_profit - 1) * 100, 2)

        # 估值：PE 按年化净利润口径（报告期不足一年时年化处理）
        pe = self._calc_pe(latest, quote_data)
        if pe is not None:
            result.valuation["pe"] = pe

        # 生成分析洞察（同比缺失时给出说明而非假增速）
        result.insights = self._generate_insights(result, statements)
        if len(statements) >= 2 and not result.growth:
            result.insights.append("历史报告期不足（缺上年同期），无法计算同比增速")

        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _period_key(period: str) -> tuple:
        """报告期排序键：'2026-Q2' → (2026, 2)"""
        try:
            year, q = period.split("-Q")
            return int(year), int(q)
        except (ValueError, AttributeError):
            return 0, 0

    @classmethod
    def _yoy_prev(cls, latest: FinancialStatement,
                  statements: List[FinancialStatement]
                  ) -> Optional[FinancialStatement]:
        """上年同期报告期（如 2026-Q2 → 2025-Q2）；缺失时返回 None。

        不回退上一报告期：A 股季报为累计口径，Q1 对比上期 Q4 年报
        会产生跨季失真的假同比（如 -87% 的虚假下滑）。
        """
        year, q = cls._period_key(latest.period)
        target = f"{year - 1}-Q{q}"
        for s in statements:
            if s.period == target:
                return s
        return None

    @staticmethod
    def _annualize_factor(period: str) -> float:
        """报告期年化系数：Q1×4、Q2(中报)×2、Q3×4/3、年报×1"""
        if period.endswith("Q1"):
            return 4.0
        if period.endswith("Q2"):
            return 2.0
        if period.endswith("Q3"):
            return 4.0 / 3.0
        return 1.0

    def _calc_pe(self, latest: FinancialStatement,
                 quote_data: Optional[dict]) -> Optional[float]:
        if (not quote_data or latest.net_profit is None
                or latest.net_profit <= 0):
            return None
        annual_profit = latest.net_profit * self._annualize_factor(
            latest.period)  # 元
        # 口径一：直接给定总市值（亿元），换算为元后与净利润同口径
        market_cap = quote_data.get("market_cap")
        if market_cap and market_cap > 0:
            return round(market_cap * 1e8 / annual_profit, 2)
        # 口径二：最新价（元）× 总股本（亿股）= 亿元
        price = quote_data.get("latest_close")
        shares = quote_data.get("total_shares")
        if price and shares:
            cap_yuan = price * shares * 1e8  # 元
            if cap_yuan > 0:
                return round(cap_yuan / annual_profit, 2)
        return None

    def _generate_insights(
        self, result: FinanceAnalysis,
        statements: List[FinancialStatement],
    ) -> List[str]:
        """规则化洞察：每条结论均由具体指标触发，保证有据可循"""
        insights = []
        prof, growth, solvency = (result.profitability, result.growth,
                                  result.solvency)
        latest = statements[-1]

        # 盈利能力（指标缺失为 None 时不触发规则，避免误读）
        gm = prof.get("gross_margin")
        if gm is not None:
            if gm > 50:
                insights.append(f"毛利率 {gm:.1f}%，处于较高水平，议价能力强")
            elif 0 < gm < 20:
                insights.append(f"毛利率 {gm:.1f}%，偏低，需关注成本控制")

        roe = prof.get("roe")
        if roe is not None:
            if roe > 15:
                insights.append(f"ROE {roe:.1f}%，股东回报优秀（>15%）")
            elif (roe < 5 and latest.net_profit
                  and latest.net_profit > 0):
                insights.append(f"ROE {roe:.1f}%，股东回报偏弱")

        # 成长能力
        rev_g = growth.get("revenue_growth")
        if rev_g is not None:
            if rev_g > 20:
                insights.append(f"营收增速 {rev_g:.1f}%，成长性突出")
            elif rev_g < 0:
                insights.append(f"营收增速 {rev_g:.1f}%，出现下滑，需警惕")
        np_g = growth.get("net_profit_growth")
        if np_g is not None:
            if np_g > 20:
                insights.append(f"净利润增速 {np_g:.1f}%，盈利动能强劲")
            elif np_g < 0:
                insights.append(f"净利润增速 {np_g:.1f}%，盈利承压")

        # 偿债与现金流
        debt = solvency.get("debt_ratio")
        if debt is not None and debt > 70:
            insights.append(f"资产负债率 {debt:.1f}%，杠杆偏高，"
                            "需关注偿债压力")
        cf_ratio = solvency.get("cashflow_to_profit")
        if cf_ratio is not None:
            if cf_ratio >= 1:
                insights.append(f"经营现金流/净利润 {cf_ratio:.2f}，"
                                "利润现金含量充足")
            elif cf_ratio < 0.5:
                insights.append(f"经营现金流/净利润仅 {cf_ratio:.2f}，"
                                "盈利质量待观察")

        if not insights:
            insights.append("各项财务指标处于常规区间，未见显著异动")
        return insights
