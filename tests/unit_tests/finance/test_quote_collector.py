# -*- coding: utf-8 -*-
"""QuoteCollector 单元测试：数据源降级链、行情属性、批量容错（全部 mock，无网络请求）"""

import pytest

from collectors.quote_collector import QuoteCollector, QuoteData, QuoteRecord


def _records(n=3):
    return [
        QuoteRecord(date=f"2026-01-0{i}", open=10.0 + i, close=10.0 + i,
                    high=11.0 + i, low=9.0 + i, volume=1000.0 * i,
                    change_pct=1.0)
        for i in range(1, n + 1)
    ]


class TestQuoteData:
    def test_latest_close(self):
        data = QuoteData(symbol="600519", name="贵州茅台", records=_records())
        assert data.latest_close == 13.0

    def test_period_return(self):
        data = QuoteData(symbol="600519", name="贵州茅台", records=_records())
        assert data.period_return == pytest.approx((13.0 / 11.0 - 1) * 100)

    def test_period_return_empty(self):
        data = QuoteData(symbol="600519", name="贵州茅台")
        assert data.period_return == 0.0
        assert data.latest_close == 0.0

    def test_to_dict_has_source_and_collected_at(self):
        data = QuoteData(symbol="600519", name="贵州茅台", records=_records())
        data.source = "test-source"
        data.collected_at = "2026-08-17T10:00:00"
        d = data.to_dict()
        assert d["source"] == "test-source"
        assert d["collected_at"] == "2026-08-17T10:00:00"
        assert len(d["records"]) == 3


class TestFallbackChain:
    def test_akshare_volume_unit_converted_to_shares(self, monkeypatch):
        """H1 回归：akshare 成交量单位为手，应×100 换算为股（与降级源一致）"""
        import akshare as ak
        import pandas as pd

        df = pd.DataFrame([{
            "日期": "2026-08-15", "开盘": 1400.0, "收盘": 1410.0,
            "最高": 1420.0, "最低": 1395.0,
            "成交量": 25000.0,  # 手
            "涨跌幅": 0.71,
        }])
        monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kw: df)
        records = QuoteCollector()._fetch_akshare(
            "600519", "2026-08-01", "2026-08-31")
        assert records[0].volume == 2500000.0  # 股
        assert records[0].close == 1410.0

    def test_akshare_failure_falls_back_to_tencent(self, monkeypatch):
        """akshare 异常时自动降级腾讯源，并正确标注来源"""
        collector = QuoteCollector()

        def boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(collector, "_fetch_akshare", boom)
        monkeypatch.setattr(collector, "_fetch_tencent", lambda *a: _records())
        monkeypatch.setattr(collector, "_fetch_sina", lambda *a: _records())

        data = collector.collect("600519", "贵州茅台",
                                 start_date="2026-01-01", end_date="2026-01-31")
        assert len(data.records) == 3
        assert "腾讯" in data.source

    def test_all_sources_empty_returns_no_records(self, monkeypatch):
        collector = QuoteCollector()
        monkeypatch.setattr(collector, "_fetch_akshare", lambda *a: [])
        monkeypatch.setattr(collector, "_fetch_tencent", lambda *a: [])
        monkeypatch.setattr(collector, "_fetch_sina", lambda *a: [])

        data = collector.collect("600519", "贵州茅台")
        assert data.records == []
        assert data.source == ""

    def test_collect_batch_isolated_failure(self, monkeypatch):
        """单标的失败不阻断批量采集"""
        collector = QuoteCollector()

        def flaky(symbol, name="", **kw):
            if symbol == "000858":
                raise RuntimeError("rate limited")
            return QuoteData(symbol=symbol, name=name, records=_records())

        monkeypatch.setattr(collector, "collect", flaky)
        results = collector.collect_batch([("600519", "贵州茅台"),
                                           ("000858", "五粮液")])
        assert len(results) == 1
        assert results[0].symbol == "600519"


class TestSecidHelpers:
    def test_prefix_shenzhen(self):
        assert QuoteCollector._prefixed("000858") == "sz000858"

    def test_prefix_shanghai(self):
        assert QuoteCollector._prefixed("600519") == "sh600519"

    def test_prefix_beijing(self):
        assert QuoteCollector._prefixed("830799") == "bj830799"
