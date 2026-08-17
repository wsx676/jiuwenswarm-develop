# -*- coding: utf-8 -*-
"""新闻与政策采集器（迭代式 Deep Research）

采集主流财经媒体新闻与政府政策文件。
来源：新浪财经、财联社、证券时报、政府部门官网等公开渠道。

单次关键词搜索覆盖面不足，本采集器实现迭代式 Deep Research：
1. 初始查询：根据目标公司与板块生成核心搜索查询；
2. 结果分析：提取关键实体、概念与新问题；
3. 查询精炼与扩展：基于新信息生成更具体深入的查询；
4. 循环与终止：重复直到信息饱和或达到搜索深度上限。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class NewsItem:
    """新闻条目"""
    title: str
    source: str          # 来源媒体
    url: str             # 原文链接（用于溯源）
    date: str
    summary: str = ""
    sentiment: str = ""  # positive / neutral / negative

    def to_dict(self) -> dict:
        return {
            "title": self.title, "source": self.source, "url": self.url,
            "date": self.date, "summary": self.summary,
            "sentiment": self.sentiment,
        }


@dataclass
class NewsData:
    """新闻数据"""
    keyword: str
    items: List[NewsItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "count": len(self.items),
            "items": [i.to_dict() for i in self.items],
        }


class NewsCollector:
    """新闻与政策采集器"""

    # 权威来源白名单
    RELIABLE_SOURCES = [
        "新浪财经", "财联社", "证券时报", "上海证券报",
        "中国证券报", "经济日报", "人民日报", "新华网",
    ]

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def collect(
        self, keyword: str, limit: int = 20,
        max_depth: int = 3,
    ) -> NewsData:
        """迭代式 Deep Research 采集：搜索→分析→精炼→再搜，直到信息饱和"""
        data = NewsData(keyword=keyword)

        # 初始查询
        queries = self._initial_queries(keyword)
        seen_urls = set()

        for depth in range(max_depth):
            # 执行当前批查询
            for query in queries:
                for item in self._search_news(query, limit):
                    url = item.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if self._is_reliable(item.get("source", "")):
                        data.items.append(NewsItem(
                            title=item.get("title", ""),
                            source=item.get("source", ""),
                            url=url,
                            date=item.get("date", ""),
                            summary=item.get("summary", ""),
                        ))

            # 基于新信息精炼扩展查询；无新增有价值信息则信息饱和，终止
            new_queries = self._refine_queries(queries, data.items)
            if not new_queries:
                break
            queries = new_queries
        return data

    def _initial_queries(self, keyword: str) -> List[str]:
        """根据关键词生成初始查询集"""
        return [keyword, f"{keyword} 最新动态", f"{keyword} 政策"]

    def _refine_queries(
        self, queries: List[str], items: List[NewsItem]
    ) -> List[str]:
        """从已有结果提取新实体/新问题，生成下一轮更深入的查询；
        无新增有价值信息时返回空列表（信息饱和）"""
        # TODO(Day 2): 调用 LLM 从最新一批新闻标题/摘要中抽取
        # 新实体（业务名、竞对、政策关键词），生成精炼查询；
        # 若与已有查询高度重复则返回 []（信息饱和）
        return []

    def _is_reliable(self, source: str) -> bool:
        return any(s in source for s in self.RELIABLE_SOURCES)

    def _search_news(self, keyword: str, limit: int) -> List[dict]:
        """通过 MCP 搜索工具检索新闻（公开渠道）"""
        # TODO(Day 2): 通过 mcp_tool_call 调用配置的搜索 MCP 服务，
        # 返回 [{"title","source","url","date","summary"}, ...]
        return []
