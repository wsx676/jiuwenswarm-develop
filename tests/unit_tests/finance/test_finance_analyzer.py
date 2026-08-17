# -*- coding: utf-8 -*-
"""FinanceAnalyzer 单元测试：指标计算、PE 年化口径、
规则化洞察触发（纯本地计算，无网络请求）"""

import pytest

from analyzers.finance_analyzer import FinanceAnalyzer
from collectors.filing_collector import FinancialStatement


def _stmt(period, revenue=100.0, net_profit=20.0, **kw):
    return FinancialStatement(
        period=period, revenue=revenue, net_profit=net_profit,
        gross_profit=kw.get("gross_profit", revenue * 0.9),
        total_assets=kw.get("total_assets", 500.0),
        total_liabilities=kw.get("total_liabilities", 100.0),
        shareholders_equity=kw.get("equity", 400.0),
        operating_cashflow=kw.get("cf", 25.0),
        gross_margin=kw.get("gm", 90.0), net_margin=kw.get("nm", 20.0),
        roe=kw.get("roe", 25.0), debt_ratio=kw.get("dr", 20.0))


@pytest.fixture
def analyzer():
    return FinanceAnalyzer()


class TestMetrics:
    def test_empty_statements(self, analyzer):
        result = analyzer.analyze([])
        assert result.insights == ["暂无公开财务数据"]

    def test_profitability_from_disclosed_fields(self, analyzer):
        result = analyzer.analyze([_stmt("2025-Q4")])
        assert result.profitability["gross_margin"] == 90.0
        assert result.profitability["roe"] == 25.0
        assert result.profitability["roa"] == 4.0  # 20/500*100

    def test_growth_yoy(self, analyzer):
        result = analyzer.analyze([_stmt("2024-Q4", revenue=80.0,
                                         net_profit=16.0),
                                   _stmt("2025-Q4", revenue=100.0,
                                         net_profit=20.0)])
        assert result.growth["revenue_growth"] == 25.0
        assert result.growth["net_profit_growth"] == 25.0

    def test_yoy_uses_same_quarter_last_year(self, analyzer):
        """同比基准是上年同期（2025-Q2），而非上一期（2025-Q4 年报）"""
        stmts = [_stmt("2025-Q2", revenue=90.0),
                 _stmt("2025-Q4", revenue=400.0),
                 _stmt("2026-Q2", revenue=100.0)]
        result = analyzer.analyze(stmts)
        assert result.growth["revenue_growth"] == pytest.approx(11.11)

    def test_descending_input_auto_sorted(self, analyzer):
        """落盘数据为降序时应自动排序，最新期取 2026-Q1"""
        stmts = [_stmt("2026-Q1", revenue=50.0),
                 _stmt("2025-Q4", revenue=400.0),
                 _stmt("2025-Q1", revenue=40.0)]
        result = analyzer.analyze(stmts)
        assert result.growth["revenue_growth"] == 25.0  # 50/40-1

    def test_cashflow_to_profit(self, analyzer):
        result = analyzer.analyze([_stmt("2025-Q4", cf=30.0)])
        assert result.solvency["cashflow_to_profit"] == 1.5


class TestValuation:
    def test_pe_by_market_cap_annualized(self, analyzer):
        # 中报净利润 40 亿元 → 年化 80 亿；市值 2000 亿 → PE 25
        stmts = [_stmt("2025-Q2", net_profit=4.0e9)]
        result = analyzer.analyze(stmts, {"market_cap": 2000.0})
        assert result.valuation["pe"] == 25.0

    def test_pe_by_price_and_shares(self, analyzer):
        # 年报净利润 94.2 亿；1500 元 × 12.56 亿股 = 18840 亿 → PE 200
        stmts = [_stmt("2025-Q4", net_profit=9.42e9)]
        result = analyzer.analyze(stmts, {"latest_close": 1500.0,
                                          "total_shares": 12.56})
        assert result.valuation["pe"] == 200.0

    def test_annualize_factor(self, analyzer):
        f = FinanceAnalyzer._annualize_factor
        assert f("2025-Q1") == 4.0
        assert f("2025-Q2") == 2.0
        assert abs(f("2025-Q3") - 4 / 3) < 1e-9
        assert f("2025-Q4") == 1.0

    def test_no_pe_without_quote(self, analyzer):
        assert analyzer.analyze([_stmt("2025-Q4")]).valuation == {}

    def test_no_pe_when_loss(self, analyzer):
        stmts = [_stmt("2025-Q4", net_profit=-5.0)]
        assert analyzer.analyze(stmts, {"market_cap": 100.0}).valuation == {}


class TestInsights:
    def test_high_margin_and_roe(self, analyzer):
        result = analyzer.analyze([_stmt("2025-Q4", gm=91.5, roe=32.0)])
        assert any("毛利率 91.5%" in i for i in result.insights)
        assert any("ROE 32.0%" in i for i in result.insights)
        assert len(result.insights) >= 2  # 验收：≥2 条洞察

    def test_growth_insights(self, analyzer):
        result = analyzer.analyze([_stmt("2024-Q4", revenue=70.0,
                                         net_profit=12.0),
                                   _stmt("2025-Q4", revenue=100.0,
                                         net_profit=20.0)])
        assert any("营收增速 42.9%" in i for i in result.insights)
        assert any("净利润增速" in i for i in result.insights)

    def test_decline_warning(self, analyzer):
        result = analyzer.analyze([_stmt("2024-Q4", revenue=120.0),
                                   _stmt("2025-Q4", revenue=100.0)])
        assert any("下滑" in i for i in result.insights)

    def test_high_leverage_warning(self, analyzer):
        result = analyzer.analyze([_stmt("2025-Q4", dr=78.0)])
        assert any("杠杆偏高" in i for i in result.insights)

    def test_fallback_insight_never_empty(self, analyzer):
        # 所有指标处于常规区间：仍至少 1 条兜底洞察
        result = analyzer.analyze([_stmt("2025-Q4", gm=30.0, roe=10.0,
                                         dr=40.0, cf=15.0)])
        assert result.insights

    def test_to_dict_structure(self, analyzer):
        d = analyzer.analyze([_stmt("2025-Q4")]).to_dict()
        assert set(d) == {"profitability", "solvency", "operation",
                          "growth", "valuation", "insights"}
