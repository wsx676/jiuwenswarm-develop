# -*- coding: utf-8 -*-
"""数据研究 Agent（Day 2/4 实现）

职责：
1. 按任务计划调度采集层：行情/财报/新闻（迭代式 Deep Research）/RAG
2. 混合记忆管理：大表格"头尾各5行"压缩进短期记忆，
   分析结论摘要沉淀长期记忆
3. 整理论据卡片（含引用来源），供 Writer 撰写使用
4. supplement：根据 Reviewer 反馈定向补采缺失数据
"""

from typing import Optional


class ResearcherAgent:
    """数据研究 Agent"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def research(self, plan: dict) -> dict:
        """按任务计划执行数据采集与整理

        Returns:
            {
                "quote_data": dict,        # 行情数据
                "filing_data": dict,       # 财报数据
                "news_data": dict,         # 新闻数据（Deep Research）
                "knowledge_chunks": list,  # RAG 知识片段
                "claims": list,            # 论据卡片（含 citation）
                "finance_analysis": None,  # 由分析引擎填充
                "charts": list,
                "citations": list,
            }
        """
        # TODO(Day 2/4): 调度 QuoteCollector / FilingCollector /
        # NewsCollector(Deep Research) / RAGRetriever，输出结构化数据
        return {
            "quote_data": {},
            "filing_data": {},
            "news_data": {},
            "knowledge_chunks": [],
            "claims": [],
            "finance_analysis": None,
            "charts": [],
            "citations": [],
        }

    def supplement(self, research_data: dict, feedback: dict) -> dict:
        """根据 Reviewer 反馈补充缺失数据（只补缺口，不全量重采）"""
        # TODO(Day 4): 解析 feedback["issues"]，定向补采后合并返回
        return research_data
