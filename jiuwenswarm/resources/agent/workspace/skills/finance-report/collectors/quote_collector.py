# -*- coding: utf-8 -*-
"""行情数据采集器

采集股票与指数的历史行情：收盘价、成交量、涨跌幅等。
数据来源策略（可替换，便于后续接入 MCP 行情工具）：
1. 主路径：akshare stock_zh_a_hist（东方财富日线，前复权）
2. 降级路径：腾讯行情接口（前复权日线）→ 新浪行情接口
数据源均标注来源字段（source）以保证可复现。
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var/"
    "CN_MarketDataService.getKLineData"
)


@dataclass
class QuoteRecord:
    """行情记录（日线）"""
    date: str
    open: float = 0.0
    close: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    change_pct: float = 0.0

    def to_dict(self) -> dict:
        return {
            "date": self.date, "open": self.open, "close": self.close,
            "high": self.high, "low": self.low,
            "volume": self.volume, "change_pct": self.change_pct,
        }


@dataclass
class QuoteData:
    """行情数据"""
    symbol: str
    name: str
    source: str = ""                 # 数据源标注（溯源与可复现）
    collected_at: str = ""           # 采集时刻（ISO 格式，溯源用）
    records: List[QuoteRecord] = field(default_factory=list)

    @property
    def latest_close(self) -> float:
        return self.records[-1].close if self.records else 0.0

    @property
    def period_return(self) -> float:
        """区间收益率"""
        if len(self.records) < 2:
            return 0.0
        return (self.records[-1].close / self.records[0].close - 1) * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "name": self.name,
            "source": self.source,
            "collected_at": self.collected_at,
            "latest_close": self.latest_close,
            "period_return": round(self.period_return, 2),
            "records": [r.to_dict() for r in self.records],
        }


class QuoteCollector:
    """行情数据采集器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def collect(
        self, symbol: str, name: str = "",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> QuoteData:
        """采集指定区间的行情数据（A 股，默认近一年）"""
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime(
                "%Y-%m-%d"
            )

        data = QuoteData(symbol=symbol, name=name)

        # 采集策略可替换：akshare 优先，失败降级为腾讯/新浪
        records, source = self._fetch(symbol, start_date, end_date)
        data.records = records
        data.source = source
        data.collected_at = datetime.now().isoformat(timespec="seconds")
        return data

    def collect_batch(self, symbols: List[tuple]) -> List[QuoteData]:
        """按板块批量采集多只标的行情（公司池批量处理入口）"""
        results = []
        for sym, name in symbols:
            try:
                results.append(self.collect(sym, name))
            except Exception as e:  # 单标的失败不阻断批量采集
                logger.warning("行情采集失败 %s(%s): %s", sym, name, e)
        return results

    def _fetch(
        self, symbol: str, start: str, end: str
    ) -> tuple:
        """获取行情，返回 (记录列表, 数据源标注)；逐源降级"""
        strategies = [
            (self._fetch_akshare, "akshare/stock_zh_a_hist(东方财富日线)"),
            (self._fetch_tencent, "腾讯行情接口(web.ifzq.gtimg.cn, 前复权)"),
            (self._fetch_sina, "新浪行情接口(quotes.sina.cn)"),
        ]
        for fetch, source in strategies:
            try:
                records = fetch(symbol, start, end)
                if records:
                    return records, source
            except Exception as e:
                logger.warning("%s 失败 %s: %s", source, symbol, e)
        return [], ""

    def _fetch_akshare(
        self, symbol: str, start: str, end: str
    ) -> List[QuoteRecord]:
        """akshare 日线行情（前复权），列：日期/开/收/高/低/量/额/幅/涨跌幅/涨跌额/换手率"""
        import akshare as ak

        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            adjust="qfq",
        )
        records = []
        for _, row in df.iterrows():
            records.append(QuoteRecord(
                date=str(row["日期"]),
                open=float(row["开盘"]), close=float(row["收盘"]),
                high=float(row["最高"]), low=float(row["最低"]),
                volume=float(row["成交量"]), change_pct=float(row["涨跌幅"]),
            ))
        return records

    def _fetch_tencent(
        self, symbol: str, start: str, end: str
    ) -> List[QuoteRecord]:
        """腾讯前复权日线（降级路径），行：[日期,开,收,高,低,量(手)]"""
        import requests

        session = requests.Session()
        session.trust_env = False  # 境内站点直连，绕过系统代理
        code = self._prefixed(symbol)
        resp = session.get(TENCENT_KLINE_URL, params={
            "param": f"{code},day,{start},{end},640,qfq",
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
        node = resp.json()["data"][code]
        klines = node.get("qfqday") or node.get("day") or []
        records = []
        prev_close = None
        for k in klines:
            o, c, h, l, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
            pct = (c / prev_close - 1) * 100 if prev_close else 0.0
            records.append(QuoteRecord(
                date=k[0], open=o, close=c, high=h, low=l,
                volume=v * 100, change_pct=round(pct, 4),
            ))
            prev_close = c
        return records

    def _fetch_sina(
        self, symbol: str, start: str, end: str
    ) -> List[QuoteRecord]:
        """新浪日线（二级降级路径，不复权），行：day/open/high/low/close/volume"""
        import requests

        session = requests.Session()
        session.trust_env = False
        resp = session.get(SINA_KLINE_URL, params={
            "symbol": self._prefixed(symbol), "scale": "240",
            "ma": "no", "datalen": "640",
        }, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn",
        }, timeout=20)
        resp.raise_for_status()
        # 响应为 jsonp：var([{...},...]); 截取数组部分解析
        text = resp.text
        items = json.loads(text[text.index("["):text.rindex("]") + 1])
        records = []
        prev_close = None
        for it in items:
            day = it["day"]
            if not (start <= day <= end):
                continue
            c = float(it["close"])
            pct = (c / prev_close - 1) * 100 if prev_close else 0.0
            records.append(QuoteRecord(
                date=day, open=float(it["open"]), close=c,
                high=float(it["high"]), low=float(it["low"]),
                volume=float(it["volume"]), change_pct=round(pct, 4),
            ))
            prev_close = c
        return records

    @staticmethod
    def _prefixed(symbol: str) -> str:
        """A 股代码加交易所前缀（沪 sh / 深 sz / 北 bj）"""
        if symbol.startswith(("6", "9", "5")):
            return f"sh{symbol}"
        if symbol.startswith(("4", "8")):
            return f"bj{symbol}"
        return f"sz{symbol}"
