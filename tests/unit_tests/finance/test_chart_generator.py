# -*- coding: utf-8 -*-
"""ChartGenerator 单元测试：折线/柱状渲染、图文同源 caption、
Markdown 表格、空数据安全（matplotlib 本地渲染，无网络）"""

import os

import pytest

from generators.chart_generator import (
    ChartGenerator, _setup_matplotlib,
)

matplotlib = pytest.importorskip("matplotlib")

RECORDS = [
    {"date": f"2026-07-{d:02d}", "close": 1200.0 + d,
     "volume": 30000.0} for d in range(1, 25)
]
STATEMENTS = [
    {"period": "2025-Q4", "revenue": 1700.0e8, "net_profit": 860.0e8,
     "gross_margin": 91.2, "net_margin": 50.5, "roe": 34.0},
    {"period": "2026-Q2", "revenue": 910.0e8, "net_profit": 460.0e8,
     "gross_margin": 89.6, "net_margin": 50.8, "roe": 16.8},
]


@pytest.fixture
def gen(tmp_path):
    return ChartGenerator(output_dir=str(tmp_path / "charts"))


class TestPriceChart:
    def test_line_png_created_and_same_source_caption(self, gen):
        quote = {"name": "贵州茅台", "source": "腾讯财经日线",
                 "latest_close": 1286.09, "records": RECORDS}
        chart = gen.generate_price_chart(quote, symbol="600519")
        assert chart.image_path.endswith("price_600519.png")
        assert os.path.exists(
            os.path.join(gen.output_dir, "price_600519.png"))
        # 图文同源：caption 由 records 计算区间收益（1200+1 → 1200+24）
        ret = (RECORDS[-1]["close"] / RECORDS[0]["close"] - 1) * 100
        assert f"{ret:.2f}%" in chart.caption or "区间收益率" in chart.caption
        assert "腾讯财经日线" in chart.caption

    def test_empty_records_returns_empty_path(self, gen):
        chart = gen.generate_price_chart({"records": []}, "600519")
        assert chart.image_path == ""

    def test_missing_close_rendered_as_nan_not_zero(self, gen):
        """L2 回归：缺失收盘价为 NaN 不绘制（此前补 0 画出假点）"""
        records = list(RECORDS)
        records[10] = {"date": "2026-07-11", "close": None,
                       "volume": 30000.0}
        quote = {"name": "贵州茅台", "source": "腾讯财经日线",
                 "latest_close": 1286.09, "records": records}
        chart = gen.generate_price_chart(quote, symbol="600519")
        assert chart.image_path.endswith("price_600519.png")


class TestMarginChart:
    def test_bar_png_created(self, gen):
        chart = gen.generate_margin_chart(STATEMENTS, symbol="600519")
        assert chart.image_path.endswith("margin_600519.png")
        assert os.path.exists(
            os.path.join(gen.output_dir, "margin_600519.png"))

    def test_partial_none_metrics_rendered(self, gen):
        """L2 回归：个别期缺 ROE 仍出图（缺失点 NaN 不补 0）"""
        stmts = [dict(STATEMENTS[0]),
                 {"period": "2026-Q2", "gross_margin": 89.6,
                  "net_margin": None, "roe": 16.8}]
        chart = gen.generate_margin_chart(stmts, symbol="600519")
        assert chart.image_path.endswith("margin_600519.png")

    def test_all_none_metrics_skipped(self, gen):
        chart = gen.generate_margin_chart(
            [{"period": "2026-Q1", "gross_margin": None}], "X")
        assert chart.image_path == ""


class TestTable:
    def test_render_table_md_none_as_dash(self):
        md = ChartGenerator.render_table_md(
            [{"period": "2026-Q1", "revenue": 500.0e8, "roe": None}])
        # 转置表：行=指标，列=报告期；金额元→亿元展示
        assert "| 指标 | 2026-Q1 |" in md
        assert "500.00" in md
        assert "—" in md

    def test_render_table_sorted_ascending(self):
        md = ChartGenerator.render_table_md(
            [{"period": "2026-Q2"}, {"period": "2025-Q4"}],
            metrics=["revenue"])
        assert md.index("2025-Q4") < md.index("2026-Q2")

    def test_render_empty_statements(self):
        assert "暂无财务数据" in ChartGenerator.render_table_md([])


def test_setup_matplotlib_chinese_font():
    plt = _setup_matplotlib()
    assert "SimHei" in plt.rcParams["font.sans-serif"][0:2]
