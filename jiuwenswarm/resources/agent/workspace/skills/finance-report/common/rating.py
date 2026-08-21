# -*- coding: utf-8 -*-
"""统一评级词汇表模块（优化方案 5，来源：TradingAgents rating.py 实践）

问题：公司研报「买入/增持」、行业研报「超配/标配」、investor 隐式
仓位评级，多文件各写各的词汇——按类型区分校验时易口径漂移返工。

设计：writer/reviewer/investor 三处 import 同一模块：
- 公司评级五档：买入/增持/持有/减持/卖出
- 板块配置三档：超配/标配/低配
- 仓位决策三态：full/partial/empty（与 decision.json 对齐）
- parse_* 确定性解析器：文本 → 规范化档位（别名归一 + default 兜底）

确定性承诺：本技能研报历史口径含描述性档位（如「中性」「减持观望」），
模块以别名表归一而非改写产物文本——已交付研报口径逐字节不变。
"""

from typing import Optional

# 公司评级五档（规范词表；解析输出统一归一到此）
COMPANY_RATINGS = ("买入", "增持", "持有", "减持", "卖出")
BUY, OVERWEIGHT, HOLD, REDUCE, SELL = COMPANY_RATINGS

# 板块配置三档
SECTOR_ALLOCATIONS = ("超配", "标配", "低配")
OVER_ALLOCATE, STANDARD_ALLOCATE, UNDER_ALLOCATE = SECTOR_ALLOCATIONS

# 仓位决策三态（与 decision.json position_decision 字段对齐）
POSITION_DECISIONS = ("full", "partial", "empty")
FULL, PARTIAL, EMPTY = POSITION_DECISIONS

# 公司研报历史描述性档位 → 五档归一（「中性/减持观望」为已交付
# 研报口径，产物文本不改写，仅解析时归一）
RATING_ALIASES = {
    "中性": HOLD,
    "减持观望": REDUCE,
    "持有观望": HOLD,
    "增持观望": OVERWEIGHT,
}

# 行业研报历史描述性档位 → 三档归一
ALLOCATION_ALIASES = {
    "低配或观望": UNDER_ALLOCATE,
}

# 本技能公司研报历史展示档位（与已交付研报口径一致，产物文本不改写；
# parse_rating 解析时归一到五档规范词）——中性档对应持有、
# 减持观望档对应减持
NEUTRAL_LABEL = "中性"
REDUCE_WATCH_LABEL = "减持观望"

# 景气度等级 → 板块配置（行业研报投资结论章口径，与 writer 规则一致）
PROSPERITY_ALLOCATION = {
    "景气向上": OVER_ALLOCATE,
    "平稳运行": STANDARD_ALLOCATE,
}


def _parse(text, terms: dict, default: str) -> str:
    """确定性解析：词表 + 别名按词长降序最长匹配，未命中返回 default"""
    text = str(text or "")
    for term in sorted(terms, key=len, reverse=True):
        if term in text:
            return terms[term]
    return default


def parse_rating(text, default: str = HOLD) -> str:
    """公司评级解析（文本 → 五档规范词；未识别返回 default）"""
    terms = {r: r for r in COMPANY_RATINGS}
    terms.update(RATING_ALIASES)
    return _parse(text, terms, default)


def parse_allocation(text, default: str = STANDARD_ALLOCATE) -> str:
    """板块配置解析（文本 → 三档规范词；未识别返回 default）"""
    terms = {a: a for a in SECTOR_ALLOCATIONS}
    terms.update(ALLOCATION_ALIASES)
    return _parse(text, terms, default)


def parse_position(value, default: Optional[str] = None) -> str:
    """仓位决策解析（full/partial/empty；非法值返回 default，
    default 为 None 时抛 ValueError——fail-loud 场景用）"""
    if value in POSITION_DECISIONS:
        return value
    if default is None:
        raise ValueError(
            f"仓位决策 {value!r} 非法，可选: {'/'.join(POSITION_DECISIONS)}")
    return default


def sector_allocation(level: str,
                      default: str = "低配或观望") -> str:
    """景气度等级 → 板块配置建议（未映射等级返回 default 描述性档位）"""
    return PROSPERITY_ALLOCATION.get(level, default)
