# -*- coding: utf-8 -*-
"""公司披露文件采集器

采集上市公司定期财报与临时公告。
数据来源策略（可替换，便于后续接入 MCP 披露工具）：
1. 财报摘要：akshare stock_financial_abstract（行=指标，列=报告期），
   覆盖利润表/资产负债表/现金流核心科目及毛利率/净利率/ROE/资产负债率
2. 公告列表：东方财富公告接口（标题/日期/链接，供新闻采集与溯源复用）
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

EASTMONEY_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
ANN_DETAIL_URL = "https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"

_QUARTER = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}


@dataclass
class FinancialStatement:
    """财务报表数据

    缺失指标为 None（而非 0.0 哨兵），避免污染推导与洞察：
    ROE 缺失时不应解读为"股东回报偏弱"，负债率缺失时不应推导总资产。
    """
    period: str                    # 报告期 YYYY-Qn
    # 利润表
    revenue: Optional[float] = None          # 营业收入
    net_profit: Optional[float] = None       # 净利润（归母）
    gross_profit: Optional[float] = None     # 毛利润
    # 资产负债表
    total_assets: Optional[float] = None     # 总资产
    total_liabilities: Optional[float] = None  # 总负债
    shareholders_equity: Optional[float] = None  # 股东权益
    # 现金流量表
    operating_cashflow: Optional[float] = None  # 经营现金流
    # 衍生指标
    gross_margin: Optional[float] = None     # 毛利率(%)
    net_margin: Optional[float] = None       # 净利率(%)
    roe: Optional[float] = None              # 净资产收益率(%)
    debt_ratio: Optional[float] = None       # 资产负债率(%)

    def to_dict(self) -> dict:
        def _r(x: Optional[float]):
            return None if x is None else round(x, 4)
        return {
            "period": self.period,
            "revenue": self.revenue, "net_profit": self.net_profit,
            "gross_profit": self.gross_profit,
            "total_assets": self.total_assets,
            "total_liabilities": self.total_liabilities,
            "shareholders_equity": self.shareholders_equity,
            "operating_cashflow": self.operating_cashflow,
            "gross_margin": _r(self.gross_margin),
            "net_margin": _r(self.net_margin),
            "roe": _r(self.roe),
            "debt_ratio": _r(self.debt_ratio),
        }


@dataclass
class FilingData:
    """公司披露数据"""
    symbol: str
    source: str = ""               # 数据源标注（溯源与可复现）
    collected_at: str = ""         # 采集时刻（ISO 格式，溯源用）
    statements: List[FinancialStatement] = field(default_factory=list)
    announcements: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "collected_at": self.collected_at,
            "statements": [s.to_dict() for s in self.statements],
            "announcements": self.announcements,
        }


class FilingCollector:
    """公司披露采集器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def collect(
        self, symbol: str, periods: int = 8
    ) -> FilingData:
        """采集最近 N 个报告期的财务数据与近期公告"""
        from datetime import datetime

        data = FilingData(symbol=symbol)
        data.statements = self._fetch_financials(symbol, periods)
        data.source = "akshare/stock_financial_abstract(东方财富F10财务摘要)"
        data.collected_at = datetime.now().isoformat(timespec="seconds")
        data.announcements = self._fetch_announcements(symbol)
        return data

    def _fetch_financials(
        self, symbol: str, periods: int
    ) -> List[FinancialStatement]:
        """解析财务摘要：行=指标、列=报告期，提取最近 N 期核心科目"""
        import math

        import akshare as ak

        df = ak.stock_financial_abstract(symbol=symbol)
        # 报告期列：YYYYMMDD，倒序（最新在前）；指标取"常用指标"分组
        period_cols = [c for c in df.columns if len(str(c)) == 8 and str(c).isdigit()]
        period_cols = sorted(period_cols, reverse=True)[:periods]

        metrics = {}
        common = df[df["选项"] == "常用指标"]
        for _, row in common.iterrows():
            metrics.setdefault(str(row["指标"]).strip(), row)

        def value(metric: str, period: str) -> Optional[float]:
            """指标值；缺失/非数值/非有限值返回 None（保留缺失语义）"""
            row = metrics.get(metric)
            if row is None or period not in row.index:
                return None
            v = row[period]
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) else None

        statements = []
        for period in period_cols:
            revenue = value("营业总收入", period)
            cost = value("营业成本", period)
            equity = value("股东权益合计(净资产)", period)
            debt_ratio = value("资产负债率", period)   # %
            # 摘要未直接给出总资产/总负债，由净资产与资产负债率推导；
            # 任一缺失或退化（负债率>=100%）时不推导，保持 None
            total_assets = None
            if (equity is not None and equity > 0
                    and debt_ratio is not None and debt_ratio < 100):
                total_assets = equity / (1 - debt_ratio / 100)
            total_liabilities = (
                total_assets - equity if total_assets is not None else None)
            statements.append(FinancialStatement(
                period=self._fmt_period(period),
                revenue=revenue,
                net_profit=value("归母净利润", period),
                gross_profit=(revenue - cost if revenue is not None
                              and cost is not None else None),
                total_assets=total_assets,
                total_liabilities=total_liabilities,
                shareholders_equity=equity,
                operating_cashflow=value("经营现金流量净额", period),
                gross_margin=value("毛利率", period),
                net_margin=value("销售净利率", period),
                roe=value("净资产收益率(ROE)", period),
                debt_ratio=debt_ratio,
            ))
        return statements

    def _fetch_announcements(
        self, symbol: str, limit: int = 15
    ) -> List[dict]:
        """抓取近期公告（标题/日期/链接），失败时降级为空列表"""
        import requests

        try:
            # 东方财富为境内站点：trust_env=False 绕过系统代理，避免代理导致连接失败
            session = requests.Session()
            session.trust_env = False
            resp = session.get(EASTMONEY_ANN_URL, params={
                "sr": "-1", "page_size": str(limit), "page_index": "1",
                "ann_type": "A", "client_source": "web",
                "stock_list": symbol, "f_node": "0", "s_node": "0",
            }, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            resp.raise_for_status()
            items = (resp.json().get("data") or {}).get("list") or []
        except Exception as e:
            logger.warning("公告采集失败 %s: %s", symbol, e)
            return []

        announcements = []
        for it in items:
            code = str(it.get("art_code", ""))
            announcements.append({
                "title": str(it.get("title", "")).split(":")[-1].strip(),
                "date": str(it.get("notice_date", ""))[:10],
                "url": ANN_DETAIL_URL.format(code=symbol, art_code=code),
            })
        return announcements

    @staticmethod
    def _fmt_period(period: str) -> str:
        """20260630 -> 2026-Q2"""
        return f"{period[:4]}-{_QUARTER.get(period[4:], period[4:])}"
