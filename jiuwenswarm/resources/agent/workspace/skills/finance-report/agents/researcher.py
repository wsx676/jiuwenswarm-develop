# -*- coding: utf-8 -*-
"""数据研究 Agent

职责：
1. 按任务计划调度采集层：行情/财报/新闻（迭代式 Deep Research），
   落盘数据优先复用（reports/finance-report/data/ 缓存），缺失再实时采集
2. 调度分析引擎：财务/宏观/行业（行业分析含同板块竞对财报横向对比）
3. 生成图文同源图表（ChartGenerator：折线/柱状/财务表格）
4. 整理论据卡片与来源清单（含引用来源），供 Writer/Reviewer 使用
5. supplement：根据 Reviewer 反馈定向补采缺失数据（Day 4 扩展）
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 采集数据落盘目录（缓存即数据资产：可复现、可断点续采）
DEFAULT_DATA_DIR = os.path.abspath(os.path.join(
    _SKILL_ROOT, *[".."] * 6, "reports", "finance-report", "data"))


class ResearcherAgent:
    """数据研究 Agent"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.data_dir = self.config.get("data_dir", DEFAULT_DATA_DIR)

    # ------------------------------------------------------------------
    def research(self, plan: dict) -> dict:
        """按任务计划执行数据采集、分析引擎调度与图表生成"""
        symbol = plan.get("target", "")
        name = plan.get("name", "")

        # 1. 采集三件套（缓存优先：落盘 JSON 复用，避免重复网络请求）
        quote_data = self._collect(
            "quote", symbol,
            lambda: self._collect_quote(symbol, name))
        filing_data = self._collect(
            "filing", symbol, lambda: self._collect_filing(symbol))
        news_data = self._collect(
            "news", symbol, lambda: self._collect_news(name or symbol))

        # 2. 分析引擎：财务 / 宏观 / 行业（竞对财报横向对比）
        statements = self._to_statements(filing_data)
        finance_analysis = None
        industry_analysis = None
        try:
            from analyzers.finance_analyzer import FinanceAnalyzer
            finance_analysis = FinanceAnalyzer().analyze(
                statements, quote_data)
        except Exception as e:  # noqa: BLE001
            logger.warning("财务分析失败: %s", e)
        try:
            from analyzers.industry_analyzer import IndustryAnalyzer
            industry_analysis = IndustryAnalyzer().analyze(
                symbol, plan.get("pool") or {}, news_data=news_data,
                peer_metrics=self._peer_metrics(
                    symbol, plan.get("competitors") or [],
                    plan.get("pool") or {}),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("行业分析失败: %s", e)
        macro_analysis = None
        try:
            from analyzers.macro_analyzer import MacroAnalyzer
            macro_analysis = MacroAnalyzer().analyze(news_data)
        except Exception as e:  # noqa: BLE001
            logger.warning("宏观分析失败: %s", e)

        # 3. 图文同源图表（渲染失败降级为空路径，报告正文仍可生成）
        stmt_dicts = [s.to_dict() for s in statements]
        charts = []
        try:
            from generators.chart_generator import ChartGenerator
            gen = ChartGenerator(
                output_dir=self.config.get("chart_dir"))
            charts = [
                gen.generate_price_chart(quote_data, symbol),
                gen.generate_margin_chart(stmt_dicts, symbol),
                gen.generate_finance_table(stmt_dicts),
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("图表生成失败: %s", e)

        return {
            "quote_data": quote_data,
            "filing_data": filing_data,
            "news_data": news_data,
            "knowledge_chunks": [],
            "claims": [],
            "finance_analysis": finance_analysis,
            "industry_analysis": industry_analysis,
            "macro_analysis": macro_analysis,
            "charts": charts,
            "citations": [],
        }

    # ------------------------------------------------------------------
    # 采集调度（缓存优先）
    # ------------------------------------------------------------------
    def _collect(self, kind: str, symbol: str, collect_fn) -> dict:
        """读缓存 {symbol}_{kind}.json；缺失则实时采集并落盘"""
        os.makedirs(self.data_dir, exist_ok=True)
        path = os.path.join(self.data_dir, f"{symbol}_{kind}.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("缓存 %s 读取失败，重新采集: %s", path, e)
        data = collect_fn()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError as e:  # noqa: BLE001 落盘失败不影响内存数据
            logger.warning("数据落盘失败 %s: %s", path, e)
        return data

    @staticmethod
    def _collect_quote(symbol: str, name: str) -> dict:
        from collectors.quote_collector import QuoteCollector
        return QuoteCollector().collect(symbol, name).to_dict()

    @staticmethod
    def _collect_filing(symbol: str) -> dict:
        from collectors.filing_collector import FilingCollector
        return FilingCollector().collect(symbol).to_dict()

    @staticmethod
    def _collect_news(keyword: str) -> dict:
        from collectors.news_collector import NewsCollector
        return NewsCollector().collect(keyword).to_dict()

    # ------------------------------------------------------------------
    @staticmethod
    def _to_statements(filing_data: dict) -> list:
        """落盘财报 dict 重建 FinancialStatement 序列"""
        from collectors.filing_collector import FinancialStatement
        return [
            FinancialStatement(**{k: v for k, v in stmt.items()
                                  if k in FinancialStatement.__dataclass_fields__})
            for stmt in filing_data.get("statements", [])
        ]

    def _peer_metrics(self, symbol: str, competitors: list,
                      pool: dict) -> dict:
        """同板块竞对最新期财务指标（含标的自身；单位：亿元/%）

        采集走同一缓存通道（{code}_filing.json），复跑不重复请求。
        """
        names = {s: n for items in (pool or {}).values()
                 for s, n in items}
        metrics = {}
        for code in [symbol] + list(competitors):
            filing = self._collect(
                "filing", code, lambda c=code: self._collect_filing(c))
            stmts = filing.get("statements", [])
            if not stmts:
                continue
            latest = max(stmts, key=lambda s: str(s.get("period", "")))
            metrics[code] = {
                "name": names.get(code, ""),
                "revenue": self._yi(latest.get("revenue")),
                "net_profit": self._yi(latest.get("net_profit")),
                "gross_margin": latest.get("gross_margin"),
                "net_margin": latest.get("net_margin"),
                "roe": latest.get("roe"),
            }
        return metrics

    @staticmethod
    def _yi(value) -> Optional[float]:
        """元 → 亿元（None 保持 None）"""
        return None if value is None else round(value / 1e8, 2)

    # ------------------------------------------------------------------
    def supplement(self, research_data: dict, feedback: dict) -> dict:
        """根据 Reviewer 反馈补充缺失数据（只补缺口，不全量重采）"""
        # Day 4：解析 feedback["issues"]，定向补采后合并返回
        return research_data
