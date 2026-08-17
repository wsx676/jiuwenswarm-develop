# -*- coding: utf-8 -*-
"""行业分析器

聚焦行业景气度、竞争格局、产业链分析。
竞对识别直接复用公司池的六大板块分组——同板块公司天然为竞对，
做板块内两两对比，无需模型猜测竞对。

输出结构与财务分析器类似：结构化指标 + 分析洞察。
"""

from dataclasses import dataclass, field
from typing import Dict, List

from ..collectors.pool_loader import find_sector, sector_peers


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
        news_data: dict = None,
    ) -> IndustryAnalysis:
        """分析标的所在板块的景气度与竞争格局

        Args:
            symbol: 目标标的代码（必须在公司池白名单内）
            pool: 公司池（{板块: [(代码, 简称)]}）
            news_data: 新闻采集结果（用于景气度与政策判断）
        """
        result = IndustryAnalysis()

        # 板块归属与竞对：直接取公司池分组，无需模型猜测
        result.sector = find_sector(pool, symbol) or ""
        result.peers = sector_peers(pool, symbol)
        if not result.sector:
            result.insights.append("标的不在组委会公司池内，无法做行业分析")
            return result

        # 行业景气度与竞争格局分析
        result.prosperity = self._assess_prosperity(result.sector, news_data)
        result.competition = self._assess_competition(symbol, result.peers)
        result.insights = self._generate_insights(result)
        return result

    def _assess_prosperity(
        self, sector: str, news_data: dict = None
    ) -> dict:
        """评估板块景气度（结合新闻/政策数据）"""
        # TODO(Day 2): 统计板块相关新闻数量与情绪分布，
        # 结合政策关键词判断景气方向
        return {}

    def _assess_competition(
        self, symbol: str, peers: List[tuple]
    ) -> dict:
        """竞争格局：板块内两两对比（财务指标/市值/市占率）"""
        # TODO(Day 2): 拉取竞对财务指标，与标的做板块内横向对比
        return {}

    def _generate_insights(self, result: IndustryAnalysis) -> List[str]:
        insights = []
        if result.sector:
            insights.append(
                f"标的属于「{result.sector}」板块，"
                f"板块内竞对 {len(result.peers)} 家"
            )
        return insights
