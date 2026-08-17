# -*- coding: utf-8 -*-
"""FilingCollector 单元测试：财务摘要解析、报告期转换、公告容错（mock akshare）"""

from unittest.mock import MagicMock

import pytest

from collectors.filing_collector import FilingCollector


@pytest.fixture
def fake_abstract(monkeypatch):
    """构造 stock_financial_abstract 返回结构：行=指标（选项/指标/各报告期列）"""
    import pandas as pd

    def make_df():
        return pd.DataFrame([
            {"选项": "常用指标", "指标": "营业总收入",
             "20260630": 100.0, "20251231": 380.0, "20250630": 350.0},
            {"选项": "常用指标", "指标": "营业成本",
             "20260630": 40.0, "20251231": 150.0, "20250630": 140.0},
            {"选项": "常用指标", "指标": "归母净利润",
             "20260630": 50.0, "20251231": 190.0, "20250630": 175.0},
            {"选项": "常用指标", "指标": "股东权益合计(净资产)",
             "20260630": 800.0, "20251231": 780.0, "20250630": 760.0},
            {"选项": "常用指标", "指标": "经营现金流量净额",
             "20260630": 60.0, "20251231": 220.0, "20250630": 200.0},
            {"选项": "常用指标", "指标": "毛利率",
             "20260630": 60.0, "20251231": 60.5, "20250630": 61.0},
            {"选项": "常用指标", "指标": "销售净利率",
             "20260630": 50.0, "20251231": 50.0, "20250630": 50.0},
            {"选项": "常用指标", "指标": "净资产收益率(ROE)",
             "20260630": 6.25, "20251231": 24.4, "20250630": 23.0},
            {"选项": "常用指标", "指标": "资产负债率",
             "20260630": 20.0, "20251231": 22.0, "20250630": 24.0},
            # 非常用指标分组应被忽略
            {"选项": "每股指标", "指标": "毛利率",
             "20260630": 999.0, "20251231": 999.0, "20250630": 999.0},
        ])

    ak = MagicMock()
    ak.stock_financial_abstract = MagicMock(side_effect=lambda symbol: make_df())
    monkeypatch.setitem(__import__("sys").modules, "akshare", ak)
    return ak


class TestFetchFinancials:
    def test_periods_limit_and_order(self, fake_abstract):
        statements = FilingCollector()._fetch_financials("600519", periods=2)
        # 最新报告期在前
        assert [s.period for s in statements] == ["2026-Q2", "2025-Q4"]

    def test_metric_mapping(self, fake_abstract):
        latest = FilingCollector()._fetch_financials("600519", periods=1)[0]
        assert latest.period == "2026-Q2"
        assert latest.revenue == 100.0
        assert latest.net_profit == 50.0
        assert latest.gross_profit == 60.0            # 营收 - 营业成本
        assert latest.operating_cashflow == 60.0
        assert latest.gross_margin == 60.0
        assert latest.roe == 6.25
        assert latest.debt_ratio == 20.0

    def test_total_assets_derived_from_equity_and_debt_ratio(self, fake_abstract):
        """总资产/总负债由净资产与资产负债率推导"""
        latest = FilingCollector()._fetch_financials("600519", periods=1)[0]
        # 净资产 800，负债率 20% -> 总资产 = 800/(1-0.2) = 1000
        assert latest.total_assets == pytest.approx(1000.0)
        assert latest.total_liabilities == pytest.approx(200.0)

    def test_ignores_non_common_metric_group(self, fake_abstract):
        latest = FilingCollector()._fetch_financials("600519", periods=1)[0]
        assert latest.gross_margin != 999.0


class TestFmtPeriod:
    def test_quarters(self):
        assert FilingCollector._fmt_period("20260331") == "2026-Q1"
        assert FilingCollector._fmt_period("20260630") == "2026-Q2"
        assert FilingCollector._fmt_period("20260930") == "2026-Q3"
        assert FilingCollector._fmt_period("20261231") == "2026-Q4"


class TestAnnouncements:
    def test_network_failure_degrades_to_empty(self, monkeypatch):
        """公告接口失败降级为空列表，不抛异常"""
        import requests as _r

        monkeypatch.setattr(
            _r.Session, "get",
            MagicMock(side_effect=_r.ConnectionError("boom")),
        )
        announcements = FilingCollector()._fetch_announcements("600519")
        assert announcements == []

    def test_announcement_fields(self, monkeypatch):
        import requests as _r

        resp = MagicMock()
        resp.json.return_value = {"data": {"list": [
            {"title": "贵州茅台:贵州茅台2026年半年度报告摘要",
             "notice_date": "2026-08-15 00:00:00",
             "art_code": "AN202608"},
        ]}}
        monkeypatch.setattr(_r.Session, "get", MagicMock(return_value=resp))
        announcements = FilingCollector()._fetch_announcements("600519")
        assert announcements == [{
            "title": "贵州茅台2026年半年度报告摘要",
            "date": "2026-08-15",
            "url": "https://data.eastmoney.com/notices/detail/600519/AN202608.html",
        }]


class TestCollect:
    def test_collect_sets_source_and_collected_at(self, fake_abstract, monkeypatch):
        monkeypatch.setattr(
            FilingCollector, "_fetch_announcements",
            lambda self, symbol, limit=15: [],
        )
        data = FilingCollector().collect("600519", periods=1)
        assert "akshare" in data.source
        assert data.collected_at  # 采集时刻已记录
        assert data.to_dict()["collected_at"] == data.collected_at
