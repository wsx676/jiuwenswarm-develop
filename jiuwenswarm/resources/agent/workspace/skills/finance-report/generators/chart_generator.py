# -*- coding: utf-8 -*-
"""多模态图表生成器

生成研报所需的各类图表：
- 股价/指数走势图（折线图）
- 财务指标对比图（柱状图）
- 财务报表表格（Markdown 表格）
- 估值对比图（条形图）

图表与正文使用同一份数据源，确保图文一致。
"""

import os
from dataclasses import dataclass
from typing import List


@dataclass
class Chart:
    """图表对象"""
    title: str
    chart_type: str         # line / bar / table / pie
    data: dict              # 图表数据（与正文同源）
    image_path: str = ""    # 生成的图片路径
    caption: str = ""       # 图注
    source: str = ""        # 数据来源（溯源）


class ChartGenerator:
    """图表生成器"""

    def __init__(
        self,
        output_dir: str = "workspace/agent/reports/finance-report/charts",
    ):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_price_chart(
        self, quote_data: dict, title: str = "股价走势"
    ) -> Chart:
        """生成股价走势图"""
        chart = Chart(
            title=title, chart_type="line",
            data=quote_data, source=quote_data.get("name", ""),
        )
        chart.image_path = self._render_line(
            quote_data.get("records", []), title
        )
        chart.caption = (
            f"数据来源：{quote_data.get('name', '公开行情数据')}；"
            f"区间收益率：{quote_data.get('period_return', 0)}%"
        )
        return chart

    def generate_finance_table(
        self, statements: List[dict], title: str = "财务数据概览"
    ) -> Chart:
        """生成财务数据表格（Markdown 表格也是多模态的一种）"""
        chart = Chart(
            title=title, chart_type="table",
            data={"statements": statements},
            source="公司定期财报",
        )
        chart.caption = "数据来源：交易所信息披露平台"
        return chart

    def generate_margin_chart(
        self, statements: List[dict], title: str = "盈利能力趋势"
    ) -> Chart:
        """生成毛利率/净利率趋势图"""
        chart = Chart(
            title=title, chart_type="bar",
            data={"statements": statements},
        )
        chart.image_path = self._render_bar(statements, title)
        chart.caption = "数据来源：公司定期财报"
        return chart

    def _render_line(self, records: list, title: str) -> str:
        """渲染折线图（使用 matplotlib 开源库）"""
        # TODO(Day 3): matplotlib 绘制收盘价走势并保存 PNG 到 output_dir，
        # 配置中文字体（SimHei），返回图片相对路径
        return ""

    def _render_bar(self, statements: list, title: str) -> str:
        """渲染柱状图"""
        # TODO(Day 3): matplotlib 绘制毛利率/净利率/ROE 多期对比柱状图
        return ""
