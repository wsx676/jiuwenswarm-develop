# -*- coding: utf-8 -*-
"""数据研究 Agent

职责：
1. 按任务计划调度采集层：行情/财报/新闻（迭代式 Deep Research），
   落盘数据优先复用（reports/finance-report/data/ 缓存），缺失再实时采集
2. 调度分析引擎：财务/宏观/行业（行业分析含同板块竞对财报横向对比）
3. 检索财务知识库（RAGRetriever：估值/分析框架方法论文档）
4. 生成图文同源图表（ChartGenerator：折线/柱状/财务表格）
5. 整理论据卡片与来源清单（含引用来源），供 Writer/Reviewer 使用
6. supplement：按 Reviewer 反馈定向补采缺失数据（只补缺口不全量重采）
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
        """按任务计划执行数据采集、分析引擎调度与图表生成

        Day 4：collect_tasks 计划驱动（Planner 拆解什么采什么），
        RAG 检索知识片段与来源清单随研究结果一并返回。
        """
        symbol = plan.get("target", "")
        name = plan.get("name", "")
        tasks = plan.get("collect_tasks") or []

        # 1. 按计划采集（缓存优先：落盘 JSON 复用，避免重复网络请求）
        quote_data = (
            self._collect("quote", symbol,
                          lambda: self._collect_quote(symbol, name))
            if "quote" in tasks else {})
        filing_data = (
            self._collect("filing", symbol,
                          lambda: self._collect_filing(symbol))
            if "filing" in tasks else {})
        news_data = (
            self._collect("news", symbol,
                          lambda: self._collect_news(name or symbol))
            if "news" in tasks else {})

        # 2. RAG 检索：估值/分析方法论知识片段（注入 Writer 材料）
        knowledge_chunks = []
        if "rag" in tasks:
            try:
                knowledge_chunks = self._retrieve_knowledge(plan)
            except Exception as e:  # noqa: BLE001 知识增强失败不阻断
                logger.warning("RAG 检索失败，降级无知识增强: %s", e)

        # 3+4. 分析引擎 + 图文同源图表（与采集解耦，供 Swarmflow
        #      「采集 / 分析」两阶段复用）
        result = {
            "quote_data": quote_data,
            "filing_data": filing_data,
            "news_data": news_data,
            "knowledge_chunks": knowledge_chunks,
            "claims": [],
        }
        result.update(self._analyze(
            plan, quote_data, filing_data, news_data))
        # RAG 知识片段纳入来源清单（analyze_cached 无知识检索，走空）
        result["citations"] = self._build_citations(
            quote_data, filing_data, news_data, knowledge_chunks)
        return result

    # ------------------------------------------------------------------
    # 采集 / 分析两阶段拆分（Swarmflow 确定性工作流的阶段状态传递）
    # ------------------------------------------------------------------
    def collect_only(self, plan: dict) -> dict:
        """「采集」阶段：只拉数据不跑分析引擎（缓存优先，断点续采）

        返回 {kind: data}；采集失败直接抛出，由调用方按标的重试。
        """
        symbol = plan.get("target", "")
        name = plan.get("name", "")
        tasks = plan.get("collect_tasks") or []
        return {
            "quote": (
                self._collect("quote", symbol,
                              lambda: self._collect_quote(symbol, name))
                if "quote" in tasks else {}),
            "filing": (
                self._collect("filing", symbol,
                              lambda: self._collect_filing(symbol))
                if "filing" in tasks else {}),
            "news": (
                self._collect("news", symbol,
                              lambda: self._collect_news(name or symbol))
                if "news" in tasks else {}),
        }

    def analyze_cached(self, plan: dict) -> dict:
        """「分析」阶段：读已采集落盘数据跑分析引擎（缺数据不重采）

        依赖「采集」阶段的缓存产物；确定性规则输出，供 Investor
        因子打分复用（research_data 结构与 research() 兼容）。
        """
        symbol = plan.get("target", "")
        quote_data = self._load_cache("quote", symbol)
        filing_data = self._load_cache("filing", symbol)
        news_data = self._load_cache("news", symbol)
        result = {
            "quote_data": quote_data,
            "filing_data": filing_data,
            "news_data": news_data,
            "knowledge_chunks": [],
            "claims": [],
        }
        # M4 修复：评分路径不渲染图表（研报路径 research() 仍渲染）
        result.update(self._analyze(
            plan, quote_data, filing_data, news_data, with_charts=False))
        return result

    def _load_cache(self, kind: str, symbol: str) -> dict:
        """直读采集缓存 {symbol}_{kind}.json；缺失或损坏返回空"""
        path = os.path.join(self.data_dir, f"{symbol}_{kind}.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("缓存 %s 读取失败: %s", path, e)
            return {}

    def _analyze(self, plan: dict, quote_data: dict, filing_data: dict,
                 news_data: dict, with_charts: bool = True) -> dict:
        """分析引擎调度 + 图表生成（research / analyze_cached 共用）"""
        symbol = plan.get("target", "")
        # 分析引擎：按 analyze_tasks 门控（计划驱动，行业/宏观
        # 研报不跑财务分析，避免无谓的引擎调用与网络请求）
        analyze = plan.get("analyze_tasks") or []
        statements = self._to_statements(filing_data)
        finance_analysis = None
        industry_analysis = None
        peer_metrics = {}
        if "finance" in analyze:
            try:
                from analyzers.finance_analyzer import FinanceAnalyzer
                finance_analysis = FinanceAnalyzer().analyze(
                    statements, quote_data)
            except Exception as e:  # noqa: BLE001
                logger.warning("财务分析失败: %s", e)
        if "industry" in analyze:
            try:
                from analyzers.industry_analyzer import IndustryAnalyzer
                anchor, competitors = symbol, plan.get("competitors") or []
                # 行业研报：target 为板块名（在公司池内），取板块首只
                # 标的作分析锚点、其余标的作竞对（板块级横向对比）
                pool = plan.get("pool") or {}
                if symbol in pool and pool[symbol]:
                    anchor = pool[symbol][0][0]
                    competitors = [s for s, _ in pool[symbol][1:]]
                peer_metrics = self._peer_metrics(anchor, competitors, pool)
                industry_analysis = IndustryAnalyzer().analyze(
                    anchor, pool, news_data=news_data,
                    peer_metrics=peer_metrics,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("行业分析失败: %s", e)
        macro_analysis = None
        if "macro" in analyze:
            try:
                from analyzers.macro_analyzer import MacroAnalyzer
                macro_analysis = MacroAnalyzer().analyze(news_data)
            except Exception as e:  # noqa: BLE001
                logger.warning("宏观分析失败: %s", e)

        # M4 修复：评分路径（analyze_cached）不渲染图表，批量分析
        # 阶段跳过 ~100 张与决策无关的 PNG；图表在研报阶段独占生成
        # Day 6：公司价/盈利/表格三图仅公司研报适用；行业研报板块
        # 对比图由 ReportWriter 按 peer_metrics 生成（图文同源）
        stmt_dicts = [s.to_dict() for s in statements]
        charts = []
        report_type = plan.get("report_type", "company")
        if with_charts and report_type == "company":
            # 图文同源图表（渲染失败降级为空路径，报告正文仍可生成）
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
            "finance_analysis": finance_analysis,
            "industry_analysis": industry_analysis,
            "macro_analysis": macro_analysis,
            # Day 6：行业研报板块对比图与竞对表同源数据透传给 Writer；
            # report_type 透传给 Reviewer（结构校验章节集按类型区分）
            "peer_metrics": peer_metrics,
            "report_type": report_type,
            "charts": charts,
            "citations": self._build_citations(
                quote_data, filing_data, news_data, []),
        }

    # ------------------------------------------------------------------
    # RAG 知识检索
    # ------------------------------------------------------------------
    def _retrieve_knowledge(self, plan: dict) -> list:
        """检索财务方法论知识库：按研报类型组合检索词取 top 片段

        冷启动自动播种内置文档；调用方 research() 已包降级防护。
        """
        rtype = plan.get("report_type", "company")
        query_map = {
            "company": f"{plan.get('sector') or ''} 估值方法 财务分析框架",
            "industry": f"{plan.get('target', '')} 行业研究框架 竞争格局",
            "macro": "宏观经济分析 GDP CPI PMI 政策解读框架",
        }
        from collectors.rag_retriever import RAGRetriever
        retriever = RAGRetriever(self.config)
        retriever.ensure_kb()
        return [c.to_dict() for c in retriever.retrieve(
            query_map.get(rtype, query_map["company"]),
            top_k=int(self.config.get("rag_top_k", 3)))]

    @staticmethod
    def _build_citations(quote: dict, filing: dict, news: dict,
                         chunks: list) -> list:
        """来源清单（溯源）：行情/财报/新闻源 + 新闻白名单源 + 知识库"""
        cites = []
        for label, d in (("行情数据", quote), ("财务数据", filing)):
            if d.get("source"):
                cites.append(f"{label}：{d['source']}")
        seen = set()
        for it in (news.get("items", []) or []):
            src = it.get("source", "")
            if src and src not in seen:
                seen.add(src)
                cites.append(f"新闻资讯：{src}")
        for c in chunks:
            tag = f"财务方法论知识库：{c.get('heading') or c.get('source')}"
            if tag not in cites:
                cites.append(tag)
        return cites

    # ------------------------------------------------------------------
    # 采集调度（缓存优先）
    # ------------------------------------------------------------------
    def _collect(self, kind: str, symbol: str, collect_fn) -> dict:
        """读缓存 {symbol}_{kind}.json；缺失则实时采集并落盘

        H2 回归：行情缓存缺市值字段或市值为 None（上次采集失败
        落盘）时重新采集升级，不永远沿用残缺缓存。
        """
        os.makedirs(self.data_dir, exist_ok=True)
        path = os.path.join(self.data_dir, f"{symbol}_{kind}.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cached = json.load(f)
                if kind == "quote" and not cached.get("market_cap"):
                    logger.info("行情缓存缺市值数据，重新采集升级: %s", path)
                else:
                    return cached
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
        """按 Reviewer 反馈定向补采（只补缺口，不全量重采）

        解析 feedback["issues"] 关键词 → 定向动作：
        - 估值/市值缺口：重采行情（市值接口可能上次失败）并重跑财务分析
        - 其余（引用率/结构/合规）属 Writer 职责，留痕不重复采集
        补采动作写入 supplement_log（决策可复现）。
        """
        data = dict(research_data)
        issues = (feedback or {}).get("issues", []) or []
        log = list(data.get("supplement_log", []) or [])
        if not issues:
            data["supplement_log"] = log
            return data

        symbol = (data.get("quote_data") or {}).get("symbol", "")
        name = (data.get("quote_data") or {}).get("name", "")
        joined = "\n".join(str(i) for i in issues)

        # 估值缺口：市值/股本缺失导致 PE 不可算 → 重采行情升级缓存
        valuation_gap = (
            ("估值" in joined or "市值" in joined)
            and symbol and not (data.get("quote_data") or {}).get(
                "market_cap"))
        if valuation_gap:
            try:
                fresh = self._collect_quote(symbol, name)
                if fresh.get("market_cap"):
                    data["quote_data"] = fresh
                    self._persist("quote", symbol, fresh)
                    log.append(f"重采行情补齐市值: {symbol}")
                    from analyzers.finance_analyzer import FinanceAnalyzer
                    data["finance_analysis"] = FinanceAnalyzer().analyze(
                        self._to_statements(data.get("filing_data") or {}),
                        fresh)
                else:
                    log.append(f"重采行情仍无市值，估值维持降级: {symbol}")
            except Exception as e:  # noqa: BLE001
                logger.warning("补采行情失败: %s", e)
                log.append(f"补采行情失败: {e}")
        else:
            log.append(
                f"审查 {len(issues)} 项问题属生成侧（Writer 重写），"
                f"无数据缺口不重采")
        data["supplement_log"] = log
        return data

    def _persist(self, kind: str, symbol: str, data: dict) -> None:
        """补采结果回写缓存（升级残缺落盘，与 _collect 同源）"""
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(os.path.join(self.data_dir, f"{symbol}_{kind}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError as e:  # noqa: BLE001
            logger.warning("补采落盘失败 %s: %s", symbol, e)
