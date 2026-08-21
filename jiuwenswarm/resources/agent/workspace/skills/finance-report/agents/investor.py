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

import logging
import os
from datetime import datetime
from typing import List, Optional

from common.rating import EMPTY, FULL, PARTIAL
from common.telemetry import RUN_STATS

logger = logging.getLogger(__name__)


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
        # Day 5 组合质量约束：达标标的过多时按评分取前 N（避免 30+
        # 标的微权重过度分散）；None 为不限制（兼容单研报流程）
        self.max_positions = investor_cfg.get("max_positions")
        # 决策备注（供 _save 写入决策日志，风控约束可追溯）
        self.decision_notes: List[str] = []

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
        """对研报结论打分（0-100，纯规则可复现，无随机）

        因子打分基于 research_data 中的财务/行情指标（见
        score_research）；审查未通过的研报不予配置（0 分），
        避免低质报告影响组合。
        """
        if not result.passed_review:
            return 0.0
        return self.score_research(getattr(result, "research_data", {}) or {})

    def score_research(self, research_data: dict) -> float:
        """因子打分（0-100，确定性规则，成果可复现）

        因子与权重（单标的上限/分散度由 _allocate 硬约束）：
        - 财务质量 25：ROE/毛利率/资产负债率
        - 成长性   20：营收/净利润同比
        - 估值     20：PE 合理性（负值或过高扣分）
        - 动量     15：区间涨跌幅
        - 风控     20：数据完整性/现金流质量/风险信号
        """
        finance = research_data.get("finance_analysis")
        quote = research_data.get("quote_data", {}) or {}
        score = 0.0

        # 1. 财务质量（盈利能力 + 偿债安全）
        prof = getattr(finance, "profitability", None) or {}
        solvency = getattr(finance, "solvency", None) or {}
        roe = prof.get("roe")
        if roe is not None:
            score += 10 if roe >= 15 else (7 if roe >= 8 else (4 if roe > 0 else 0))
        gross = prof.get("gross_margin")
        if gross is not None:
            score += 8 if gross >= 40 else (5 if gross >= 20 else 2)
        debt = solvency.get("debt_ratio")
        if debt is not None:
            score += 7 if debt <= 40 else (4 if debt <= 60 else 1)

        # 2. 成长性（同比口径；缺失不加分，不假增速）
        growth = getattr(finance, "growth", None) or {}
        rev_g = growth.get("revenue_growth")
        if rev_g is not None:
            score += 10 if rev_g >= 15 else (6 if rev_g >= 5 else (3 if rev_g >= 0 else 0))
        np_g = growth.get("net_profit_growth")
        if np_g is not None:
            score += 10 if np_g >= 15 else (6 if np_g >= 5 else (3 if np_g >= 0 else 0))

        # 3. 估值（PE 年化口径：负值亏损不得分，过高估值扣分）
        pe = (getattr(finance, "valuation", None) or {}).get("pe")
        if pe is not None:
            if pe <= 0:
                score += 0
            elif pe <= 25:
                score += 20
            elif pe <= 40:
                score += 12
            elif pe <= 60:
                score += 5

        # 4. 动量（区间涨跌幅）
        ret = quote.get("period_return")
        if isinstance(ret, (int, float)):
            score += 15 if ret >= 10 else (10 if ret >= 0 else (4 if ret >= -10 else 0))

        # 5. 风控与数据质量（完整性/现金流/风险信号）
        if finance is not None and quote:
            score += 10
        cf = solvency.get("cashflow_to_profit")
        if cf is not None:
            score += 6 if cf >= 0.8 else (3 if cf >= 0 else 0)
        # 风险信号仅在数据完整时计分（空数据不算风控加分）
        if finance is not None:
            risk_hits = research_data.get("risk_signals") or []
            score += 4 if not risk_hits else max(0, 4 - 2 * len(risk_hits))

        return round(min(100.0, score), 1)

    # ------------------------------------------------------------------
    # 公司池批量决策
    # ------------------------------------------------------------------

    def run_portfolio(
        self, pool_file: str, save: bool = False,
        output_dir: str = "reports/finance-report",
        research_fn=None, sector: str = "",
        scores: Optional[dict] = None,
    ) -> dict:
        """公司池批量选股与仓位配置

        流程：加载公司池白名单 → 逐标的采集分析并评分（research_fn，
        由编排器注入采集/分析流水线）→ 风控约束分配权重
        → 格式校验 → 输出 Portfolio.json（空仓时输出 {} 并记录决策逻辑）

        Args:
            sector: 板块名（单板块批量打通）；非空时只对该板块
                标的评分配置，空则全池。
            scores: 预计算评分（Swarmflow「分析」阶段缓存）；非空时
                直接复用，跳过 research_fn（阶段间状态传递，避免重复
                采集分析）。
        """
        from collectors.pool_loader import load_pool, whitelist_symbols

        pool = load_pool(pool_file)
        allowed = whitelist_symbols(pool)
        if sector:
            if sector not in pool:
                raise ValueError(
                    f"板块「{sector}」不在公司池内，可选: "
                    f"{'、'.join(pool.keys())}")
            pool = {sector: pool[sector]}
            # M1 修复：板块过滤后同步收窄白名单，保证「评分缓存复用」
            # 路径（--use-cached-scores + --sector）同样受板块约束，
            # 避免全池缓存评分越界进入单板块组合
            allowed = whitelist_symbols(pool)

        # 逐标的评分：research_fn(symbol, name) -> research_data
        # Day 5 批量容错：单标的失败自动重试一次，仍失败则记 0 分
        # 跳过并留痕（决策日志 + 遥测 failures），不阻断整体批量
        score_notes: List[str] = []
        if scores is None:
            scores = {}
            if research_fn is not None:
                for sector_items in pool.values():
                    for symbol, name in sector_items:
                        data = None
                        for attempt in range(2):  # 有限重试：至多 2 次
                            try:
                                data = research_fn(symbol, name)
                                break
                            except Exception as e:  # noqa: BLE001
                                if attempt == 0:
                                    logger.warning(
                                        "标的 %s %s 研究失败，重试一次: %s",
                                        symbol, name, e)
                                    continue
                                logger.warning(
                                    "标的 %s %s 重试仍失败，跳过: %s",
                                    symbol, name, e)
                                RUN_STATS.record_failure(
                                    f"research:{symbol}", e)
                                score_notes.append(
                                    f"标的 {symbol} {name} 采集分析重试"
                                    f"仍失败，按 0 分跳过: {str(e)[:200]}")
                        scores[symbol] = (
                            self.score_research(data)
                            if data is not None else 0.0)
        else:
            # M1 修复：复用缓存评分时同样按（板块收窄后的）白名单
            # 过滤，与实时评分分支口径一致
            scores = {s: v for s, v in scores.items() if s in allowed}

        portfolio = self._allocate(scores, allowed)
        # 评分失败留痕并入决策日志（_allocate 会重置 decision_notes）
        self.decision_notes.extend(score_notes)

        # 提交硬约束校验：代码在白名单内、权重合规
        errors = self.validate_portfolio(portfolio, allowed)
        if errors:
            raise ValueError(f"Portfolio 校验失败: {errors}")

        if save:
            self._save(portfolio, output_dir, scores, self.decision_notes)
        return portfolio

    def validate_portfolio(self, portfolio: dict, allowed: set) -> List[str]:
        """Portfolio.json 格式校验（提交硬约束）

        规则：代码须在公司池白名单内；单标权重 (0, max_weight]；
        总权重 ≤ 1.0；空仓 {} 合法（须另附决策逻辑说明）。
        返回问题清单，空列表为通过。
        """
        errors = []
        total = 0.0
        for symbol, weight in portfolio.items():
            if symbol not in allowed:
                errors.append(f"代码 {symbol} 不在公司池白名单内")
            if not isinstance(weight, (int, float)) or not (
                    0 < weight <= self.max_weight + 1e-9):
                errors.append(
                    f"{symbol} 权重 {weight} 超出 (0, {self.max_weight}] 区间")
            total += weight if isinstance(weight, (int, float)) else 0.0
        if total > 1.0 + 1e-9:
            errors.append(f"总权重 {total:.2f} 超过 1.0")
        return errors

    def _allocate(self, scores: dict, allowed: set) -> dict:
        """按评分分配仓位权重（风控约束硬校验）"""
        self.decision_notes = []
        # 1. 白名单硬校验：列表外代码直接剔除（标的越界防护）
        valid = {
            s: v for s, v in scores.items()
            if s in allowed and v >= self.score_threshold
        }
        if not valid:
            # 空仓决策：须在报告中阐明决策逻辑（由决策日志记录）
            self.decision_notes.append(
                "公司池内所有标的评分均低于阈值，空仓并阐明理由")
            return {}

        # 2. 按评分排序取前 N，归一化后截断单标的上限
        ranked = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
        # Day 5 组合质量约束：达标标的过多时取评分前 max_positions，
        # 避免微权重过度分散（调整依据回写决策日志，可追溯可复现）
        if (self.max_positions and len(ranked) > self.max_positions):
            self.decision_notes.append(
                f"持仓集中度：达标标的 {len(ranked)} 只，按评分取前 "
                f"{self.max_positions} 只配置（尾部标的评分优势不足，"
                f"微权重对组合贡献有限）")
            ranked = ranked[:self.max_positions]
        # M2 修复：分散度约束生效——达标标的不足 min_position_count
        # 时留痕说明（单研报流程天然单标的，按软约束阐明而非强制空仓）
        if len(ranked) < self.min_positions:
            self.decision_notes.append(
                f"分散度提示：达标标的仅 {len(ranked)} 只，"
                f"低于建议最少持仓 {self.min_positions} 只；"
                f"按当前达标标的配置并阐明理由")
        total_score = sum(v for _, v in ranked) or 1.0
        portfolio = {}
        for symbol, score in ranked:
            weight = min(self.max_weight, score / total_score)
            portfolio[symbol] = round(weight, 2)

        # 3. 总权重约束（≤ 1.0）：round 逐项舍入的累计误差可能使
        #    和 ≥ 1.0（如 8 只各 0.125 → 0.13×8 = 1.04，提交硬约束
        #    超限），等比缩放 + 末位吸收残差保证不超限
        total = sum(portfolio.values())
        if total > 1.0 - 1e-9:
            portfolio = {
                s: round(w / total, 2) for s, w in portfolio.items()
            }
            last = next(reversed(portfolio))  # dict 保序，取末位
            portfolio[last] = round(
                1.0 - sum(w for s, w in portfolio.items()
                          if s != last), 2)
        return portfolio

    def _position_stance(self, portfolio: dict,
                         scores: dict) -> tuple:
        """仓位决策与理由（赛题要求：满仓/半仓/空仓均须阐明决策逻辑）

        理由由确定性规则基于真实评分与配置结果生成（禁止编造），
        口径与 _allocate 风控约束一致：入选均分、达标/未达标标的数、
        总权重与现金保留比例。

        Returns:
            (position_decision, position_rationale)
            decision ∈ {"full", "partial", "empty"}
        """
        total_w = round(sum(portfolio.values()), 2)
        n_scores = len(scores)
        if not portfolio:
            return EMPTY, (
                f"公司池 {n_scores} 只标的均未达到 "
                f"{self.score_threshold:.0f} 分入选阈值"
                f"（数据不足或基本面承压），不具备配置条件，"
                f"空仓为防御性决策，等待基本面与市场信号改善")
        held_scores = [scores.get(s, 0.0) for s in portfolio]
        avg = sum(held_scores) / len(held_scores)
        above = sum(1 for v in scores.values()
                    if v >= self.score_threshold)
        below = n_scores - above
        if total_w >= 0.95:
            return FULL, (
                f"入选 {len(portfolio)} 只标的（平均评分 {avg:.1f}）；"
                f"池内 {n_scores} 只中 {above} 只达到 "
                f"{self.score_threshold:.0f} 分阈值，达标覆盖充分，"
                f"权重归一化至 {total_w:.2f} 满仓配置")
        return PARTIAL, (
            f"入选 {len(portfolio)} 只标的（平均评分 {avg:.1f}，"
            f"总权重 {total_w:.2f}）；池内 {n_scores} 只中仅 "
            f"{above} 只达到 {self.score_threshold:.0f} 分入选阈值，"
            f"其余 {below} 只未达配置标准，按规则不强制满仓，"
            f"保留 {round((1.0 - total_w) * 100)}% 现金应对"
            f"宏观与市场波动")

    def _save(self, portfolio: dict, output_dir: str, scores: dict,
              notes: Optional[List[str]] = None) -> None:
        """保存 Portfolio.json 与决策日志（成果可复现性；方案 11 原子写）"""
        from common.file_io import atomic_write_json
        atomic_write_json(
            os.path.join(output_dir, "Portfolio.json"), portfolio)

        # 决策日志：评分、权重、仓位决策与理由、空仓理由、分散度提示留痕
        decision, rationale = self._position_stance(portfolio, scores)
        log_dir = os.path.join(output_dir, "decision_log")
        os.makedirs(log_dir, exist_ok=True)
        log = {
            # L4 修复：决策日志写入时间戳，与 run_stats 同口径，
            # 便于第三方复现时对齐运行批次
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scores": scores,
            "portfolio": portfolio,
            # 赛题要求：满仓/半仓/空仓均须在报告中阐明决策逻辑
            "position_decision": decision,
            "position_rationale": rationale,
            "empty_position": len(portfolio) == 0,
            "empty_reason": (
                "公司池内所有标的评分均低于阈值，不具备投资价值"
                if not portfolio else ""
            ),
            "notes": notes or [],
        }
        atomic_write_json(os.path.join(log_dir, "decision.json"), log)
