# -*- coding: utf-8 -*-
"""任务规划 Agent（Day 4 实现）

职责：
1. 判断研报类型（company / industry / macro）
2. 拆解采集与分析子任务，制定采集计划
3. 公司研报场景下校验标的在公司池白名单内，并确定同板块竞对名单
"""

from typing import Optional


class PlannerAgent:
    """任务规划 Agent"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def plan(self, request) -> dict:
        """根据请求生成任务计划 JSON

        Returns:
            {
                "report_type": str,
                "target": str,
                "sector": str,            # 所属板块（来自公司池分组）
                "competitors": [str],     # 同板块竞对代码列表
                "collect_tasks": [str],   # 采集子任务：quote/filing/news/rag
                "analyze_tasks": [str],   # 分析子任务：finance/industry/macro
            }
        """
        # TODO(Day 4): 接入公司池加载（collectors.pool_loader）做白名单校验
        # 与板块竞对提取；接入 LLM 做任务拆解
        return {
            "report_type": request.report_type,
            "target": request.target,
            "sector": "",
            "competitors": [],
            "collect_tasks": ["quote", "filing", "news", "rag"],
            "analyze_tasks": ["finance", "industry", "macro"],
        }
