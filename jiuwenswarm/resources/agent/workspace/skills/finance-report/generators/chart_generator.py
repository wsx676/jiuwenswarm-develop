# -*- coding: utf-8 -*-
"""多模态图表生成器

生成研报所需的各类图表：
- 股价/指数走势图（折线图）
- 盈利能力趋势图（毛利率/净利率/ROE 多期分组柱状图）
- 财务报表表格（Markdown 表格）

图表与正文使用同一份数据源，确保图文一致（图文同源）；
matplotlib 统一配置中文字体（SimHei/Microsoft YaHei）与 Agg 后端。
"""

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# 默认图表输出目录：reports/finance-report/charts（与编排器输出对齐）
DEFAULT_CHART_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), *[".."] * 7,
    "reports", "finance-report", "charts"))


def _setup_matplotlib():
    """导入 matplotlib 并配置中文字体（Agg 后端，进程内只配一次）"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = [
        "SimHei", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


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

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = output_dir or DEFAULT_CHART_DIR
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def generate_price_chart(
        self, quote_data: dict, symbol: str = "",
        title: str = "股价走势（近一年收盘价）",
    ) -> Chart:
        """生成股价走势折线图（图文同源：区间收益由 records 计算）"""
        records = quote_data.get("records", [])
        source = quote_data.get("source", "公开行情数据")
        chart = Chart(
            title=title, chart_type="line",
            data={"symbol": symbol,
                  "points": len(records),
                  "latest_close": quote_data.get("latest_close"),
                  "source": source},
            source=f"{quote_data.get('name', '')}行情（{source}）",
        )
        period_return = quote_data.get("period_return")
        if period_return is None and records:
            first, last = records[0], records[-1]
            if first.get("close") and last.get("close"):
                period_return = round(
                    (last["close"] / first["close"] - 1) * 100, 2)
        chart.image_path = self._render_line(records, symbol, title)
        chart.caption = (
            f"数据来源：{source}；区间收益率：{period_return}%；"
            f"最新收盘：{quote_data.get('latest_close', '—')}"
        )
        return chart

    def generate_margin_chart(
        self, statements: List[dict], symbol: str = "",
        title: str = "盈利能力趋势（毛利率/净利率/ROE）",
    ) -> Chart:
        """生成盈利趋势柱状图：毛利率/净利率/ROE 多期对比"""
        chart = Chart(
            title=title, chart_type="bar",
            data={"symbol": symbol, "statements": statements},
            source="公司定期财报",
        )
        chart.image_path = self._render_bar(statements, symbol, title)
        chart.caption = "数据来源：公司定期财报（披露口径指标）"
        return chart

    def generate_finance_table(
        self, statements: List[dict], title: str = "财务数据概览",
        metrics: Optional[List[str]] = None,
    ) -> Chart:
        """生成财务数据 Markdown 表格（表格也是多模态的一种）"""
        chart = Chart(
            title=title, chart_type="table",
            data={"statements": statements},
            source="公司定期财报",
        )
        chart.caption = self.render_table_md(statements, metrics)
        return chart

    # ------------------------------------------------------------------
    @staticmethod
    def render_table_md(
        statements: List[dict],
        metrics: Optional[List[str]] = None,
    ) -> str:
        """把财报序列渲染为 Markdown 表（行=指标，列=报告期，升序）

        落盘金额单位为元（akshare 原始口径），展示层换算为亿元。
        """
        if not statements:
            return "（暂无财务数据）"
        stmts = sorted(
            statements, key=lambda s: str(s.get("period", "")))
        metrics = metrics or [
            "revenue", "net_profit", "gross_margin",
            "net_margin", "roe", "debt_ratio",
        ]
        labels = {
            "period": "报告期", "revenue": "营业收入(亿元)",
            "net_profit": "净利润(亿元)", "gross_profit": "毛利润(亿元)",
            "total_assets": "总资产(亿元)", "net_margin": "净利率(%)",
            "gross_margin": "毛利率(%)", "roe": "ROE(%)",
            "debt_ratio": "资产负债率(%)",
            "operating_cashflow": "经营现金流(亿元)",
        }
        # 金额字段（元）展示时换算为亿元（单位与标签一致，图文同源）
        amount_keys = {
            "revenue", "net_profit", "gross_profit", "total_assets",
            "total_liabilities", "shareholders_equity",
            "operating_cashflow",
        }
        lines = ["| 指标 | " + " | ".join(
            str(s.get("period", "")) for s in stmts) + " |"]
        lines.append("|" + "---|" * (len(stmts) + 1))
        for key in metrics:
            label = labels.get(key, key)
            row = [label]
            for s in stmts:
                v = s.get(key)
                if key in amount_keys and v is not None:
                    v = v / 1e8
                row.append("—" if v is None
                           else f"{v:,.2f}" if isinstance(v, (int, float))
                           else str(v))
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def _render_line(self, records: List[dict], symbol: str,
                     title: str) -> str:
        """渲染股价折线图：近 250 个交易日，保存 PNG 返回相对路径"""
        if not records:
            return ""
        try:
            plt = _setup_matplotlib()
            recent = records[-250:]
            dates = [r.get("date", "") for r in recent]
            # L2 修复：缺失收盘价为 NaN 不绘制（不画 0 假点，
            # 与全链路 None 语义一致）
            closes = [r.get("close") if r.get("close") is not None
                      else float("nan") for r in recent]
            fig, ax = plt.subplots(figsize=(10, 4.5), dpi=150)
            ax.plot(range(len(closes)), closes, lw=1.2, color="#1f4e9c")
            step = max(1, len(dates) // 6)
            ax.set_xticks(range(0, len(dates), step))
            ax.set_xticklabels(dates[::step], fontsize=8)
            ax.set_ylabel("收盘价（元）")
            ax.set_title(title, fontsize=12)
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fname = f"price_{symbol or 'index'}.png"
            path = os.path.join(self.output_dir, fname)
            fig.savefig(path)
            plt.close(fig)
            return self._relative(path)
        except Exception as e:  # noqa: BLE001 绘图失败不阻断报告生成
            logger.warning("股价折线图渲染失败: %s", e)
            return ""

    def _render_bar(self, statements: List[dict], symbol: str,
                    title: str) -> str:
        """渲染盈利趋势分组柱状图：毛利率/净利率/ROE × 报告期"""
        stmts = [s for s in statements
                 if any(s.get(k) is not None
                        for k in ("gross_margin", "net_margin", "roe"))]
        if not stmts:
            return ""
        try:
            import numpy as np
            plt = _setup_matplotlib()
            stmts = sorted(stmts, key=lambda s: str(s.get("period", "")))
            periods = [str(s.get("period", "")) for s in stmts]
            series = [
                ("毛利率(%)", "gross_margin", "#1f4e9c"),
                ("净利率(%)", "net_margin", "#c8a24a"),
                ("ROE(%)", "roe", "#4a7c59"),
            ]
            x = np.arange(len(periods))
            width = 0.25
            fig, ax = plt.subplots(figsize=(10, 4.5), dpi=150)
            for i, (label, key, color) in enumerate(series):
                # L2 修复：缺失指标为 NaN 不绘制（个别期缺 ROE
                # 不画 "ROE=0" 假柱，与全链路 None 语义一致）
                values = [s.get(key) if s.get(key) is not None
                          else float("nan") for s in stmts]
                ax.bar(x + (i - 1) * width, values, width,
                       label=label, color=color)
            ax.set_xticks(x)
            ax.set_xticklabels(periods, fontsize=8)
            ax.set_ylabel("百分比（%）")
            ax.set_title(title, fontsize=12)
            ax.legend(fontsize=9)
            ax.grid(axis="y", alpha=0.3)
            fig.tight_layout()
            fname = f"margin_{symbol or 'company'}.png"
            path = os.path.join(self.output_dir, fname)
            fig.savefig(path)
            plt.close(fig)
            return self._relative(path)
        except Exception as e:  # noqa: BLE001
            logger.warning("盈利趋势图渲染失败: %s", e)
            return ""

    def _relative(self, path: str) -> str:
        """返回相对 reports/finance-report/ 的路径（报告引用口径）"""
        base = os.path.abspath(os.path.join(self.output_dir, ".."))
        return os.path.relpath(path, base).replace("\\", "/")
