# -*- coding: utf-8 -*-
"""NewsQualityFilter 单元测试：三阶段质量过滤 Pipeline（方案 1）

覆盖：默认开关口径 / Stage 0 FastPass / Stage 1 规则粗筛 /
Stage 2 LLM 精评（mock，无真实调用）/ 遥测留痕 / researcher 接线。
"""

from unittest.mock import MagicMock

from collectors.news_filter import NewsQualityFilter
from common.telemetry import RunStats

ENABLED = {"news_filter": {"enabled": True}}
# Stage 2 精评须显式开启（默认关，控制 Token 成本）
LLM_ENABLED = {"news_filter": {"enabled": True, "llm_grade_enabled": True}}


def _news(items, keyword="贵州茅台"):
    return {"keyword": keyword, "count": len(items), "items": items}


def _item(title, source="某某自媒体", summary=""):
    return {"title": title, "source": source, "url": "http://a.com/x",
            "date": "2026-08-17", "summary": summary}


class TestSwitches:
    def test_default_disabled(self):
        flt = NewsQualityFilter({})
        assert not flt.enabled
        assert flt.rerank_enabled          # Stage 1 默认开（随总开关生效）
        assert not flt.llm_grade_enabled   # Stage 2 默认关（Token 成本）

    def test_disabled_returns_unchanged(self):
        data = _news([_item("任意标题")])
        assert NewsQualityFilter({}).filter(data) is data

    def test_empty_items_unchanged(self):
        data = _news([])
        assert NewsQualityFilter(ENABLED).filter(data) is data


class TestFastPass:
    def test_few_reliable_items_pass_directly(self):
        """Stage 0：≤2 条且全权威来源 → 直通，不走规则粗筛"""
        stats = RunStats()
        # 无主题词也应保留（FastPass 优先于 Stage 1）
        items = [_item("股东大会通知", source="财联社"),
                 _item("董事长致辞", source="证券时报")]
        out = NewsQualityFilter(ENABLED).filter(
            _news(items), stats=stats)
        assert len(out["items"]) == 2
        assert stats.news_filter["fastpass"] == 1
        assert stats.news_filter["rule_removed"] == 0

    def test_all_removed_fallback_keeps_original(self):
        """全滤净兜底：规则过严时宁用旧口径，不降级'数据不足'"""
        stats = RunStats()
        items = [_item("股东大会通知", source="某某自媒体"),
                 _item("董事长致辞", source="证券时报")]
        out = NewsQualityFilter(ENABLED).filter(
            _news(items), stats=stats)
        assert len(out["items"]) == 2
        assert stats.news_filter["rule_removed"] == 0

    def test_partial_removed_no_fallback(self):
        """部分命中时正常过滤（不触发兜底）"""
        items = [_item("贵州茅台半年报业绩增长", summary="营收"),
                 _item("茅台镇旅游攻略")]
        out = NewsQualityFilter(ENABLED).filter(
            _news(items), stats=RunStats())
        assert len(out["items"]) == 1


class TestRuleStage:
    def test_entity_and_topic_required(self):
        """Stage 1：实体词与主题词须同时命中"""
        flt = NewsQualityFilter(ENABLED)
        items = [
            # 实体 + 主题 → 保留
            _item("贵州茅台半年报业绩增长", summary="营收同比增长"),
            # 实体命中无主题词 → 过滤（纯词面命中）
            _item("茅台镇旅游打卡攻略"),
            # 主题命中无实体 → 过滤（"消费者权益"非"消费板块"实体）
            _item("消费者权益保护宣传", source="某某自媒体"),
        ]
        out = flt.filter(_news(items, keyword="贵州茅台"),
                         stats=RunStats())
        kept = [it["title"] for it in out["items"]]
        assert kept == ["贵州茅台半年报业绩增长"]
        assert out["filter_stats"] == {
            "received": 3, "kept": 1, "rule_removed": 2, "llm_removed": 0}

    def test_full_entity_name_passes_without_topic(self):
        """实体全称命中 = 强相关直通（行情动态类，如"贵州茅台跌超4%"）"""
        flt = NewsQualityFilter(ENABLED)
        items = [_item("贵州茅台,跌超4% - 今日头条", source="今日头条")]
        out = flt.filter(_news(items), stats=RunStats())
        assert len(out["items"]) == 1

    def test_sector_keyword_core_entity(self):
        """板块关键词剥离通用后缀：'消费板块' → 核心词'消费'"""
        flt = NewsQualityFilter(ENABLED)
        items = [
            _item("消费板块上市公司业绩回暖", summary="行业景气回升"),
            _item("投诉维权指南"),
        ]
        out = flt.filter(_news(items, keyword="消费板块"),
                         stats=RunStats())
        assert [it["title"] for it in out["items"]] == [
            "消费板块上市公司业绩回暖"]

    def test_rerank_can_be_disabled(self):
        cfg = {"news_filter": {"enabled": True, "rerank_enabled": False}}
        items = [_item("完全无关的内容")]
        out = NewsQualityFilter(cfg).filter(_news(items), stats=RunStats())
        assert len(out["items"]) == 1

    def test_extra_entities_member_stocks(self):
        """行业研报：板块成分股名并入实体词（板块新闻常以成分股点名）"""
        flt = NewsQualityFilter(ENABLED)
        items = [
            # 成分股名 + 主题词 → 保留（标题无"消费"字样）
            _item("贵州茅台半年报业绩增长", summary="营收同比增长"),
            # 噪声：既无实体词又无主题词 → 过滤
            _item("美债收益率飙升"),
        ]
        out = flt.filter(_news(items, keyword="消费板块"),
                         extra_entities=["贵州茅台", "伊利股份"],
                         stats=RunStats())
        assert [it["title"] for it in out["items"]] == [
            "贵州茅台半年报业绩增长"]
        assert out["filter_stats"]["rule_removed"] == 1

    def test_entity_terms(self):
        assert NewsQualityFilter._entity_terms("贵州茅台") == {
            "贵州茅台", "茅台"}
        assert NewsQualityFilter._entity_terms("消费板块") == {
            "消费板块", "消费"}
        assert NewsQualityFilter._entity_terms("") == set()


class TestLlmStage:
    def _many_items(self, n=8):
        return [_item(f"茅台 新闻{i} 业绩动态", summary="业绩")
                for i in range(n)]

    def test_threshold_filtering(self):
        """Stage 2：LLM 打分 < 0.4 阈值者被过滤"""
        llm = MagicMock()
        llm.chat_json.return_value = [0.8, 0.5, 0.1, 0.1, 0.8, 0.8, 0.5, 0.1]
        stats = RunStats()
        out = NewsQualityFilter(LLM_ENABLED).filter(
            _news(self._many_items()), llm=llm, stats=stats)
        assert len(out["items"]) == 5
        assert stats.news_filter["llm_removed"] == 3
        llm.chat_json.assert_called_once()

    def test_not_triggered_when_few_items(self):
        """条数 ≤ llm_min_items 时不触发精评（控制 Token 成本）"""
        llm = MagicMock()
        items = [_item(f"茅台 新闻{i} 业绩", summary="业绩")
                 for i in range(5)]
        out = NewsQualityFilter(ENABLED).filter(
            _news(items), llm=llm, stats=RunStats())
        llm.chat_json.assert_not_called()
        assert len(out["items"]) == 5

    def test_disabled_by_default(self):
        llm = MagicMock()
        NewsQualityFilter(ENABLED).filter(
            _news(self._many_items()), llm=llm, stats=RunStats())
        llm.chat_json.assert_not_called()

    def test_dict_format_scores(self):
        """兼容 {index, score} 对象数组输出"""
        llm = MagicMock()
        llm.chat_json.return_value = [
            {"index": 1, "score": 0.8}, {"index": 2, "score": 0.1},
            {"index": 3, "score": 0.5}, {"index": 4, "score": 0.1},
            {"index": 5, "score": 0.8}, {"index": 6, "score": 0.8},
        ]
        out = NewsQualityFilter(LLM_ENABLED).filter(
            _news(self._many_items(6)), llm=llm, stats=RunStats())
        assert len(out["items"]) == 4

    def test_llm_exception_fallback(self):
        """精评异常 → 跳过 Stage 2，保留规则层结果"""
        llm = MagicMock()
        llm.chat_json.side_effect = RuntimeError("timeout")
        items = self._many_items()
        out = NewsQualityFilter(LLM_ENABLED).filter(
            _news(items), llm=llm, stats=RunStats())
        assert len(out["items"]) == len(items)

    def test_unparseable_output_fallback(self):
        llm = MagicMock()
        llm.chat_json.return_value = {"bad": "shape"}
        items = self._many_items()
        out = NewsQualityFilter(LLM_ENABLED).filter(
            _news(items), llm=llm, stats=RunStats())
        assert len(out["items"]) == len(items)


class TestTelemetry:
    def test_summary_news_filtered(self):
        """run_stats 留痕：news_filtered = 被过滤数/召回数"""
        stats = RunStats()
        stats.add_news_filter("贵州茅台", total=18, kept=11,
                              rule_removed=5, llm_removed=2)
        s = stats.summary()
        assert s["news_filtered"] == "7/18"
        assert s["news_filter"]["received"] == 18
        assert s["news_filter"]["kept"] == 11

    def test_summary_clean_when_unused(self):
        """未过滤时 summary 不输出 news_filter 字段（旧口径不变）"""
        s = RunStats().summary()
        assert "news_filtered" not in s and "news_filter" not in s


class TestResearcherWiring:
    def test_disabled_passthrough(self):
        from agents.researcher import ResearcherAgent
        data = _news([_item("无关标题")])
        agent = ResearcherAgent({})
        assert agent._filter_news({"name": "贵州茅台"}, data) is data

    def test_enabled_filters_noise(self):
        from agents.researcher import ResearcherAgent
        items = [_item("贵州茅台半年报业绩增长", summary="营收"),
                 _item("茅台镇旅游攻略")]
        agent = ResearcherAgent(ENABLED)
        out = agent._filter_news({"name": "贵州茅台"}, _news(items))
        assert len(out["items"]) == 1

    def test_pool_member_names_injected(self):
        """行业研报：plan['pool'] 板块成分股名自动注入实体词"""
        from agents.researcher import ResearcherAgent
        items = [_item("伊利股份中报净利增长", summary="业绩"),
                 _item("美债收益率飙升")]
        plan = {"name": "消费板块", "target": "消费板块",
                "pool": {"消费板块": [("600519", "贵州茅台"),
                                       ("600887", "伊利股份")]}}
        agent = ResearcherAgent(ENABLED)
        out = agent._filter_news(plan, _news(items, keyword="消费板块"))
        assert [it["title"] for it in out["items"]] == [
            "伊利股份中报净利增长"]

    def test_empty_news_passthrough(self):
        from agents.researcher import ResearcherAgent
        agent = ResearcherAgent(ENABLED)
        assert agent._filter_news({}, {}) == {}
