# -*- coding: utf-8 -*-
"""统一评级模块单元测试（优化方案 5）

覆盖：五档/三档/三态词表、别名归一解析、最长匹配、default 兜底、
景气度映射、writer/reviewer/investor 三处接线口径。
"""

import pytest

from common.rating import (
    COMPANY_RATINGS, POSITION_DECISIONS, SECTOR_ALLOCATIONS,
    parse_allocation, parse_position, parse_rating, sector_allocation)


class TestVocab:
    def test_company_five_levels(self):
        assert COMPANY_RATINGS == ("买入", "增持", "持有", "减持", "卖出")

    def test_sector_three_levels(self):
        assert SECTOR_ALLOCATIONS == ("超配", "标配", "低配")

    def test_position_three_states(self):
        assert POSITION_DECISIONS == ("full", "partial", "empty")


class TestParseRating:
    def test_canonical_hit(self):
        assert parse_rating("建议增持，仓位 5%-10%") == "增持"

    def test_alias_neutral_to_hold(self):
        assert parse_rating("评级：中性") == "持有"

    def test_alias_reduce_watch_longest_match(self):
        # 「减持观望」须整词归一为减持，而非先命中「减持」造成歧义
        assert parse_rating("减持观望") == "减持"
        assert parse_rating("建议减持观望，暂不配置") == "减持"

    def test_unrecognized_returns_default(self):
        assert parse_rating("无评级信息") == "持有"
        assert parse_rating("", default="") == ""

    def test_none_text(self):
        assert parse_rating(None, default="卖出") == "卖出"


class TestParseAllocation:
    def test_canonical_hit(self):
        assert parse_allocation("板块配置建议：超配") == "超配"

    def test_alias_under_watch(self):
        assert parse_allocation("低配或观望") == "低配"

    def test_unrecognized_returns_default(self):
        assert parse_allocation("无配置词", default="") == ""


class TestParsePosition:
    def test_valid_values(self):
        for v in ("full", "partial", "empty"):
            assert parse_position(v) == v

    def test_invalid_with_default(self):
        assert parse_position("半仓", default="partial") == "partial"

    def test_invalid_fail_loud(self):
        with pytest.raises(ValueError):
            parse_position("半仓")


class TestSectorAllocation:
    def test_prosperity_mapping(self):
        assert sector_allocation("景气向上") == "超配"
        assert sector_allocation("平稳运行") == "标配"

    def test_unmapped_level_default(self):
        # 与 writer 历史口径一致：未映射等级输出描述性档位
        assert sector_allocation("景气承压") == "低配或观望"
        assert parse_allocation(sector_allocation("景气承压")) == "低配"


class TestReviewerWiring:
    def _draft(self, content):
        from types import SimpleNamespace
        return SimpleNamespace(content=content)

    def test_company_rating_present_passes(self):
        from agents.reviewer import ReviewerAgent
        draft = self._draft("## 二、投资结论与仓位建议\n评级建议：增持\n"
                            "## 三、公司概况\n正文")
        assert ReviewerAgent._check_rating(draft, "company") == []

    def test_company_rating_missing_flagged(self):
        from agents.reviewer import ReviewerAgent
        draft = self._draft("## 二、投资结论与仓位建议\n建议关注该标的\n"
                            "## 三、公司概况\n正文")
        issues = ReviewerAgent._check_rating(draft, "company")
        assert len(issues) == 1 and "评级词汇" in issues[0]

    def test_industry_allocation_present_passes(self):
        from agents.reviewer import ReviewerAgent
        draft = self._draft("## 二、投资结论与配置建议\n配置建议：低配或观望\n")
        assert ReviewerAgent._check_rating(draft, "industry") == []

    def test_macro_skipped(self):
        from agents.reviewer import ReviewerAgent
        draft = self._draft("## 二、宏观结论与板块配置建议\n无评级词\n")
        assert ReviewerAgent._check_rating(draft, "macro") == []

    def test_section_absent_no_double_flag(self):
        # 章节缺失由 _check_structure 负责，此处不重复计分
        from agents.reviewer import ReviewerAgent
        draft = self._draft("## 三、公司概况\n正文")
        assert ReviewerAgent._check_rating(draft, "company") == []


class TestWriterWiring:
    def test_rating_labels_unchanged(self):
        """口径不变：writer 三档输出与历史交付研报逐字一致"""
        from generators.report_writer import ReportWriter
        from analyzers.finance_analyzer import FinanceAnalysis
        w = ReportWriter({})
        pos = FinanceAnalysis(insights=["盈利改善", "毛利率提升", "现金流稳健"])
        neg = FinanceAnalysis(insights=["营收增速承压", "净利润下滑"])
        assert w._rating(pos, None, {})[0] == "增持"
        assert w._rating(neg, None, {})[0] == "减持观望"
        mid = FinanceAnalysis(insights=["盈利平稳", "杠杆偏高"])
        assert w._rating(mid, None, {})[0] == "中性"
