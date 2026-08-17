# -*- coding: utf-8 -*-
"""任务规划 Agent

职责：
1. 判断研报类型（company / industry / macro）
2. 拆解采集与分析子任务，制定采集计划
3. 公司研报场景下校验标的在公司池白名单内，并确定同板块竞对名单
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# 公司池默认路径：项目根 example/上市公司列表.xlsx（组委会公布列表）
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SKILL_ROOT, *[".."] * 6))
DEFAULT_POOL_FILE = os.path.join(
    _PROJECT_ROOT, "example", "上市公司列表.xlsx")


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
                "pool": dict,             # 公司池（内存传递，供下游复用）
                "collect_tasks": [str],   # 采集子任务：quote/filing/news/rag
                "analyze_tasks": [str],   # 分析子任务：finance/industry/macro
            }
        """
        plan = {
            "report_type": request.report_type,
            "target": request.target,
            "name": getattr(request, "name", ""),
            "sector": "",
            "competitors": [],
            "pool": {},
            "collect_tasks": ["quote", "filing", "news"],
            "analyze_tasks": ["finance", "industry", "macro"],
        }

        if request.report_type != "company":
            return plan  # 行业/宏观研报（Day 4+ 扩展）

        # 公司研报：加载公司池做白名单校验与板块竞对提取
        try:
            from collectors.pool_loader import (
                load_pool, find_sector, sector_peers, validate_symbol,
            )
            pool_file = self.config.get("pool_file", DEFAULT_POOL_FILE)
            pool = load_pool(pool_file)
            plan["pool"] = pool

            if not validate_symbol(pool, request.target):
                logger.warning("标的 %s 不在公司池白名单内", request.target)
                return plan

            plan["sector"] = find_sector(pool, request.target) or ""
            plan["competitors"] = [
                s for s, _ in sector_peers(pool, request.target)]
            plan["collect_tasks"].append("peer_filing")  # 竞对财报
        except Exception as e:  # noqa: BLE001 池加载失败不阻断（降级无板块）
            logger.warning("公司池加载失败，行业分析降级: %s", e)
        return plan
