# -*- coding: utf-8 -*-
"""投资决策 Agent（Day 4 实现）

职责：
1. 基于各公司研报结论/分析指标做投资评分（规则打分 + LLM 修正）
2. 按评分与风控约束生成仓位权重（单标的上限、分散度、支持空仓）
3. 输出 Portfolio.json（{"股票代码": 持仓占比}），标的须在公司池白名单内
4. 空仓决策时阐明决策逻辑（写入决策日志）

风控约束（与 config.yaml investor 配置对齐）：
- max_weight_per_stock: 0.4    单标的最大权重
- min_position_count:   3      建议最少持仓标的数（分散度）
- allow_empty_position: true   允许空仓（须阐明决策逻辑）
"""

import json
import os
from typing import List, Optional


class InvestorAgent:
    """投资决策 Agent"""

    DEFAULT_MAX_WEIGHT = 0.4
    DEFAULT_MIN_POSITIONS = 3
    # 入选组合的最低评分（0-100），低于该分数不配置
    DEFAULT_SCORE_THRESHOLD = 60.0

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        investor_cfg = self.config.get("investor", {})
        self.max_weight = investor_cfg.get(
            "max_weight_per_stock", self.DEFAULT_MAX_WEIGHT
        )
        self.min_positions = investor_cfg.get(
            "min_position_count", self.DEFAULT_MIN_POSITIONS
        )
        self.allow_empty = investor_cfg.get("allow_empty_position", True)
        self.score_threshold = investor_cfg.get(
            "score_threshold", self.DEFAULT_SCORE_THRESHOLD
        )

    # ------------------------------------------------------------------
    # 单标的决策（供编排器在公司研报流程末尾调用）
    # ------------------------------------------------------------------

    def decide(self, result) -> dict:
        """基于单份研报结论给出建议仓位（供参考，最终组合由 run_portfolio 汇总）"""
        score = self.score_report(result)
        if score < self.score_threshold:
            return {}
        return {result.target: round(min(self.max_weight, score / 200.0), 2)}

    def score_report(self, result) -> float:
        """对研报结论打分（0-100）

        TODO(Day 4): 规则打分（财务质量/估值/成长性/动量）+ LLM 修正；
        当前为占位实现：审查通过给基础分，按审查得分折算。
        """
        if not result.passed_review:
            return 0.0
        # TODO: 接入财务/估值/动量因子规则打分
        return 60.0

    # ------------------------------------------------------------------
    # 公司池批量决策
    # ------------------------------------------------------------------

    def run_portfolio(
        self, pool_file: str, save: bool = False,
        output_dir: str = "reports/finance-report",
    ) -> dict:
        """公司池批量选股与仓位配置

        流程：加载公司池白名单 → 逐标的评分 → 风控约束分配权重
        → 输出 Portfolio.json（空仓时输出 {} 并记录决策逻辑）
        """
        from collectors.pool_loader import load_pool, whitelist_symbols

        pool = load_pool(pool_file)
        allowed = whitelist_symbols(pool)

        # TODO(Day 4): 逐标的调用编排流程产出研报结论并评分
        scores: dict = {}  # symbol -> score

        portfolio = self._allocate(scores, allowed)

        if save:
            self._save(portfolio, output_dir, scores)
        return portfolio

    def _allocate(self, scores: dict, allowed: set) -> dict:
        """按评分分配仓位权重（风控约束硬校验）"""
        # 1. 白名单硬校验：列表外代码直接剔除（标的越界防护）
        valid = {
            s: v for s, v in scores.items()
            if s in allowed and v >= self.score_threshold
        }
        if not valid:
            # 空仓决策：须在报告中阐明决策逻辑（由决策日志记录）
            return {}

        # 2. 按评分排序取前 N，归一化后截断单标的上限
        ranked = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
        total_score = sum(v for _, v in ranked) or 1.0
        portfolio = {}
        for symbol, score in ranked:
            weight = min(self.max_weight, score / total_score)
            portfolio[symbol] = round(weight, 2)

        # 3. 总权重约束（≤ 1.0）：等比缩放
        total = sum(portfolio.values())
        if total > 1.0:
            portfolio = {
                s: round(w / total, 2) for s, w in portfolio.items()
            }
        return portfolio

    def _save(self, portfolio: dict, output_dir: str, scores: dict) -> None:
        """保存 Portfolio.json 与决策日志（成果可复现性）"""
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "Portfolio.json"), "w",
                  encoding="utf-8") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)

        # 决策日志：评分、权重、空仓理由留痕
        log_dir = os.path.join(output_dir, "decision_log")
        os.makedirs(log_dir, exist_ok=True)
        log = {
            "scores": scores,
            "portfolio": portfolio,
            "empty_position": len(portfolio) == 0,
            "empty_reason": (
                "公司池内所有标的评分均低于阈值，不具备投资价值"
                if not portfolio else ""
            ),
        }
        with open(os.path.join(log_dir, "decision.json"), "w",
                  encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
