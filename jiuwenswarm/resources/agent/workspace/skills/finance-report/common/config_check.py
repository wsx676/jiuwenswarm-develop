# -*- coding: utf-8 -*-
"""fail-loud 启动配置校验（优化方案 9，来源：TradingAgents 实践）

问题：多处 `config.get(key, default)` 静默兜底——错配（如阈值写成
字符串、权重超界、pool_file 路径拼错）不暴露，无人值守复现时产出
一份错口径的 Portfolio 才被发现。

设计：技能入口（run_report.py 初始化）对关键配置做类型/值域校验，
非法立即抛 ConfigError 退出；仅校验显式提供的配置项（未提供走各
Agent 既有默认值，口径不变）。
"""

import os
from typing import List


class ConfigError(ValueError):
    """启动配置非法（fail-loud：第一时间暴露而非静默兜底）"""


def _number(value, lo, hi, lo_inclusive=True, hi_inclusive=True):
    """数值与值域检查（bool 视为非法；返回问题描述或 None）"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"须为数值，实际 {value!r}"
    lo_ok = value >= lo if lo_inclusive else value > lo
    hi_ok = value <= hi if hi_inclusive else value < hi
    if not (lo_ok and hi_ok):
        return f"须在 [{lo}, {hi}] 区间，实际 {value!r}"
    return None


def validate_startup_config(config: dict) -> None:
    """启动配置校验：非法抛 ConfigError（含全部问题清单）

    覆盖关键配置（与 investor/pool 口径对齐）：
    - investor.score_threshold：0-100 数值（入选阈值）
    - investor.max_weight_per_stock：(0, 1] 数值（单标的上限）
    - investor.min_position_count / max_positions：正整数
    - pool_file：存在性（错路径 fail-loud，避免白名单静默失效）
    - news_filter.llm_grade_enabled / material_rescue.enabled：布尔
    """
    config = config or {}
    errors: List[str] = []

    investor = config.get("investor") or {}
    if not isinstance(investor, dict):
        errors.append(f"investor 配置须为映射，实际 {type(investor).__name__}")
        investor = {}
    checks = {
        "score_threshold": (0, 100),
        "max_weight_per_stock": (0, 1),
    }
    for key, (lo, hi) in checks.items():
        if key in investor:
            problem = _number(investor[key], lo, hi,
                              lo_inclusive=key != "max_weight_per_stock")
            if problem:
                errors.append(f"investor.{key} {problem}")
    for key in ("min_position_count", "max_positions"):
        if key in investor:
            value = investor[key]
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value < 1:
                errors.append(f"investor.{key} 须为正整数，实际 {value!r}")

    pool_file = config.get("pool_file")
    if pool_file and not os.path.exists(pool_file):
        errors.append(f"pool_file 不存在: {pool_file}")

    flags = (
        ("news_filter", "llm_grade_enabled"),
        ("material_rescue", "enabled"),
        ("news_filter", "enabled"),
    )
    for section, key in flags:
        cfg = config.get(section)
        if isinstance(cfg, dict) and key in cfg \
                and not isinstance(cfg[key], bool):
            errors.append(
                f"{section}.{key} 须为布尔值，实际 {cfg[key]!r}")

    query_variants = config.get("query_variants")
    if query_variants is not None and not isinstance(query_variants, bool):
        errors.append(f"query_variants 须为布尔值，实际 {query_variants!r}")

    if errors:
        raise ConfigError(
            "启动配置校验失败（fail-loud，拒绝静默兜底）：\n- "
            + "\n- ".join(errors))
