# -*- coding: utf-8 -*-
"""行业分析器

聚焦行业景气度、竞争格局、产业链分析。
竞对识别直接复用公司池的六大板块分组——同板块公司天然为竞对，
做板块内两两对比，无需模型猜测竞对。

输出结构与财务分析器类似：结构化指标 + 分析洞察。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    from collectors.pool_loader import find_sector, sector_peers
except ImportError:  # 兼容包导入/直跑：按绝对路径定位技能根目录
    import os
    import sys
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)
    from collectors.pool_loader import find_sector, sector_peers

logger = logging.getLogger(__name__)

# 新闻情绪规则词表（景气度信号，规则化可解释）
POSITIVE_WORDS = (
    "增长", "超预期", "新高", "提价", "回暖", "复苏", "利好", "中标",
    "回购", "增持", "分红", "扩张", "景气", "突破", "加速", "净流入",
    "创新高", "景气度", "消费提振",
)
NEGATIVE_WORDS = (
    "下滑", "低于预期", "降价", "处罚", "调查", "亏损", "减持", "质押",
    "风险", "召回", "退市", "下跌", "放缓", "承压", "监管", "警告",
    "疲软", "累库", "价格战",
)
# 政策信号关键词（景气度辅助判断）
POLICY_WORDS = (
    "政策", "规划", "补贴", "减税", "专项债", "促消费", "以旧换新",
    "监管", "整顿", "反垄断",
)

# 竞对对比指标（均与 FinanceAnalyzer 披露口径一致；None 不参与排名）
PEER_METRICS = [
    ("revenue", "营业收入(亿元)"),
    ("net_profit", "净利润(亿元)"),
    ("gross_margin", "毛利率(%)"),
    ("net_margin", "净利率(%)"),
    ("roe", "ROE(%)"),
]


@dataclass
class IndustryAnalysis:
    """行业分析结果"""
    sector: str = ""                        # 所属板块
    prosperity: dict = field(default_factory=dict)   # 行业景气度
    competition: dict = field(default_factory=dict)  # 竞争格局
    peers: List[tuple] = field(default_factory=list) # 同板块竞对
    insights: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sector": self.sector,
            "prosperity": self.prosperity,
            "competition": self.competition,
            "peers": [{"symbol": s, "name": n} for s, n in self.peers],
            "insights": self.insights,
        }


class IndustryAnalyzer:
    """行业分析器"""

    def analyze(
        self,
        symbol: str,
        pool: Dict[str, List[tuple]],
        news_data: Optional[dict] = None,
        peer_metrics: Optional[Dict[str, dict]] = None,
    ) -> IndustryAnalysis:
        """分析标的所在板块的景气度与竞争格局

        Args:
            symbol: 目标标的代码（必须在公司池白名单内）
            pool: 公司池（{板块: [(代码, 简称)]}）
            news_data: 新闻采集结果（用于景气度与政策判断）
            peer_metrics: 同板块竞对最新期财务指标
                {代码: {"name", "revenue", "net_profit",
                        "gross_margin", "net_margin", "roe"}}，
                含标的自身；指标缺失为 None（不参与排名）
        """
        result = IndustryAnalysis()

        # 板块归属与竞对：直接取公司池分组，无需模型猜测
        result.sector = find_sector(pool, symbol) or ""
        result.peers = sector_peers(pool, symbol)
        if not result.sector:
            result.insights.append("标的不在组委会公司池内，无法做行业分析")
            return result

        # 行业景气度与竞争格局分析
        result.prosperity = self._assess_prosperity(
            result.sector, news_data or {})
        result.competition = self._assess_competition(
            symbol, result.peers, peer_metrics or {})
        result.insights = self._generate_insights(symbol, result)
        return result

    # ------------------------------------------------------------------
    def _assess_prosperity(self, sector: str, news_data: dict) -> dict:
        """评估板块景气度：新闻数量 + 正负情绪词频 + 政策信号（规则化）"""
        items = news_data.get("items", [])
        text = " ".join(
            f"{it.get('title', '')} {it.get('summary', '')}" for it in items)

        pos = sum(text.count(w) for w in POSITIVE_WORDS)
        neg = sum(text.count(w) for w in NEGATIVE_WORDS)
        policy = sorted({w for w in POLICY_WORDS if w in text})
        # 情绪分：50 为中性基准，正负词频加权（截断到 0-100）
        score = max(0, min(100, 50 + pos * 5 - neg * 5))

        if not items:
            level = "数据不足"
        elif score >= 60:
            level = "景气向上"
        elif score >= 40:
            level = "平稳运行"
        else:
            level = "景气承压"
        return {
            "news_count": len(items),
            "positive_hits": pos,
            "negative_hits": neg,
            "sentiment_score": score,
            "level": level,
            "policy_signals": policy,
        }

    def _assess_competition(
        self, symbol: str, peers: List[tuple],
        peer_metrics: Dict[str, dict],
    ) -> dict:
        """竞争格局：板块内两两对比（披露口径指标横向对比表 + 排名）"""
        # 参与对比的公司：标的 + 有指标的竞对（保持板块顺序）
        companies = [(symbol, peer_metrics.get(symbol, {}).get("name", ""))]
        companies += [
            (s, peer_metrics.get(s, {}).get("name", n))
            for s, n in peers
            if s in peer_metrics
        ]
        if len(companies) < 2:
            return {"companies": [c[1] or c[0] for c in companies],
                    "table": [], "target_rank": {},
                    "leader_metrics": [],
                    "note": "竞对财务数据不足，未生成横向对比"}

        # 表头 + 指标行（值缺失显示 —，不参与排名）
        header = ["指标"] + [name or s for s, name in companies]
        table, rank, leader = [header], {}, []
        for key, label in PEER_METRICS:
            row, values = [label], []
            for s, _ in companies:
                v = peer_metrics.get(s, {}).get(key)
                values.append(v)
                row.append("—" if v is None else round(v, 2))
            table.append(row)
            # 排名：指标值降序（越大越好；None 剔除）
            valid = [(s, v) for (s, _), v in zip(companies, values)
                     if v is not None]
            if symbol in {s for s, _ in valid}:
                ordered = [s for s, _ in
                           sorted(valid, key=lambda kv: kv[1], reverse=True)]
                rank[key] = ordered.index(symbol) + 1
                if rank[key] == 1:
                    leader.append(label)
        return {
            "companies": [name or s for s, name in companies],
            "table": table,
            "target_rank": rank,
            "leader_metrics": leader,
        }

    # ------------------------------------------------------------------
    def _generate_insights(
        self, symbol: str, result: IndustryAnalysis,
    ) -> List[str]:
        """规则化洞察：板块归属 + 景气方向 + 竞对地位"""
        insights = []
        pros = result.prosperity
        if pros:
            insights.append(
                f"「{result.sector}」板块景气度判定为{pros['level']}"
                f"（情绪分 {pros['sentiment_score']}，"
                f"近 {pros['news_count']} 条相关新闻，"
                f"正面信号 {pros['positive_hits']} / "
                f"负面信号 {pros['negative_hits']}）"
            )
            if pros.get("policy_signals"):
                insights.append(
                    "板块政策信号：" + "、".join(pros["policy_signals"][:5])
                )
        insights.append(
            f"标的属于「{result.sector}」板块，板块内竞对 "
            f"{len(result.peers)} 家"
        )
        comp = result.competition
        if comp.get("target_rank"):
            n = len(comp.get("companies", []))
            rank = comp["target_rank"]
            fmt = "、".join(f"{k} 第 {v}" for k, v in rank.items())
            insights.append(f"板块内横向对比（共 {n} 家）：标的 {fmt}")
            if comp.get("leader_metrics"):
                insights.append(
                    "标的在 " + "、".join(comp["leader_metrics"])
                    + " 上居板块首位，具备相对竞争优势"
                )
        return insights
