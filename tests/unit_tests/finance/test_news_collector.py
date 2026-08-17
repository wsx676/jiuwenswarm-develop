# -*- coding: utf-8 -*-
"""NewsCollector 单元测试：迭代式 Deep Research 循环、饱和终止、
噪声过滤、来源白名单、规则降级精炼（全部 mock，无网络请求）"""

from unittest.mock import MagicMock

from collectors.news_collector import NewsCollector, NewsData, NewsItem


def _item(url, title="标题", source="新浪财经", date="2026-08-17"):
    return {"title": title, "url": url, "source": source,
            "date": date, "summary": "摘要"}


class TestInitialQueries:
    def test_initial_queries(self):
        nc = NewsCollector()
        assert nc._initial_queries("贵州茅台") == [
            "贵州茅台", "贵州茅台 最新动态", "贵州茅台 政策"]

    def test_is_reliable(self):
        nc = NewsCollector()
        assert nc._is_reliable("新浪财经")
        assert nc._is_reliable("来源：证券时报网")
        assert not nc._is_reliable("某某自媒体")


class TestIterativeLoop:
    def test_dedup_and_trace(self, monkeypatch):
        """两轮迭代：URL 去重、每轮查询与新增数留痕"""
        nc = NewsCollector()
        calls = {"n": 0}

        def fake_search(query, limit):
            calls["n"] += 1
            # 第一条 URL 重复出现，应去重
            return [_item("http://a.com/1", title=query),
                    _item(f"http://a.com/{calls['n']}-2", title=query)]

        refine_seq = [["贵州茅台 市场份额"], []]
        monkeypatch.setattr(nc, "_search_news", fake_search)
        monkeypatch.setattr(nc, "_refine_queries",
                            lambda kw, qs, items: refine_seq.pop(0))

        data = nc.collect("贵州茅台", max_depth=3)
        assert data.depth_executed == 2
        # 第 1 轮 3 查询 × 2 条 - 2 条重复 = 4；
        # 第 2 轮 1 查询 × 2 条 - 1 条跨轮重复 = 1，随后饱和终止
        assert data.count == 5
        assert len(data.search_trace) == 2
        assert data.search_trace[0]["queries"] == [
            "贵州茅台", "贵州茅台 最新动态", "贵州茅台 政策"]
        assert data.search_trace[0]["new_items"] == 4
        assert data.search_trace[1]["queries"] == ["贵州茅台 市场份额"]
        assert data.search_trace[1]["new_items"] == 1

    def test_executed_query_not_repeated(self, monkeypatch):
        """精炼查询与已执行查询重复时应被跳过"""
        nc = NewsCollector()
        searched = []
        monkeypatch.setattr(
            nc, "_search_news",
            lambda q, lim: searched.append(q) or [_item(f"http://x/{q}")])
        # 精炼返回已执行过的初始查询
        monkeypatch.setattr(
            nc, "_refine_queries",
            lambda kw, qs, items: ["贵州茅台 政策"] if len(searched) == 3 else [])
        data = nc.collect("贵州茅台", max_depth=3)
        assert searched.count("贵州茅台 政策") == 1
        assert data.depth_executed == 2

    def test_saturation_stops_iteration(self, monkeypatch):
        """新增条目低于阈值即判定信息饱和并终止"""
        nc = NewsCollector({"min_new_per_round": 2})
        round_items = [[_item(f"http://a/{i}") for i in range(5)],
                       [_item("http://a/new")]]  # 第 2 轮仅新增 1 条
        seq = iter(round_items)
        monkeypatch.setattr(nc, "_search_news",
                            lambda q, lim: next(seq, []))
        monkeypatch.setattr(nc, "_refine_queries",
                            lambda kw, qs, items: ["贵州茅台 X"])
        data = nc.collect("贵州茅台", max_depth=3)
        assert data.depth_executed == 2  # 第 2 轮饱和终止，未到 3

    def test_max_items_cap(self, monkeypatch):
        nc = NewsCollector({"max_items": 3})
        monkeypatch.setattr(
            nc, "_search_news",
            lambda q, lim: [_item(f"http://b/{q}/{i}") for i in range(10)])
        monkeypatch.setattr(nc, "_refine_queries",
                            lambda kw, qs, items: ["q2"])
        data = nc.collect("贵州茅台", max_depth=3)
        assert data.count <= 3


class TestFilters:
    def test_noise_url_filtered(self, monkeypatch):
        nc = NewsCollector()
        monkeypatch.setattr(nc, "_search_news", lambda q, lim: [
            _item("https://baike.baidu.com/item/贵州茅台"),
            _item("https://quote.eastmoney.com/sh600519.html"),
            _item("https://finance.sina.com.cn/doc-123.shtml",
                  source="新浪财经"),
        ])
        monkeypatch.setattr(nc, "_refine_queries", lambda *a: [])
        data = nc.collect("贵州茅台")
        assert data.count == 1
        assert data.items[0].url.endswith("doc-123.shtml")

    def test_strict_source_whitelist(self, monkeypatch):
        nc = NewsCollector({"strict_source": True})
        monkeypatch.setattr(nc, "_search_news", lambda q, lim: [
            _item("http://c/1", source="新浪财经"),
            _item("http://c/2", source="未知自媒体"),
        ])
        monkeypatch.setattr(nc, "_refine_queries", lambda *a: [])
        data = nc.collect("贵州茅台")
        assert data.count == 1
        assert data.items[0].source == "新浪财经"


class TestTimeBudgetAndBackoff:
    def test_time_budget_early_stop(self, monkeypatch):
        """M2 回归：超出时间预算提前终止，search_trace 留痕"""
        nc = NewsCollector({"time_budget": -1})  # 立即超预算
        searched = []
        monkeypatch.setattr(
            nc, "_search_news", lambda q, lim: searched.append(q) or [])
        data = nc.collect("贵州茅台", max_depth=3)
        assert searched == []            # 一个查询都未执行
        assert data.depth_executed == 1
        assert data.search_trace[0]["budget_exceeded"] is True

    def test_sogou_backoff_no_sleep_after_last_failure(self, monkeypatch):
        """M2 回归：搜狗第 4 次尝试仍失败后不再空等（最坏省 16s）"""
        import requests

        resp = MagicMock()
        resp.text = "<html>empty</html>"  # 无 vr-title，视为反爬拦截
        resp.raise_for_status.return_value = None
        sleeps = []
        monkeypatch.setattr(
            requests.Session, "get", lambda self, *a, **kw: resp)
        monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

        items = NewsCollector()._search_sogou("贵州茅台", 10)
        assert items == []
        assert sleeps == [4, 8, 12]      # 末次失败后不再 sleep(16)


class TestRuleRefine:
    def test_refine_by_rules_extracts_entities(self):
        nc = NewsCollector()
        items = [
            NewsItem(title="系列酒 收入大增", source="", url="1", date=""),
            NewsItem(title="系列酒 用户破亿", source="", url="2", date=""),
            NewsItem(title="无关标题", source="", url="3", date=""),
        ]
        queries = nc._refine_by_rules("贵州茅台", items)
        assert queries, "高频实体应生成精炼查询"
        assert all(q.startswith("贵州茅台") for q in queries)
        assert any("系列酒" in q for q in queries)

    def test_refine_by_rules_no_entity_returns_empty(self):
        nc = NewsCollector()
        items = [NewsItem(title="贵州茅台公告", source="", url="1", date="")]
        assert nc._refine_by_rules("贵州茅台", items) == []


class TestDataStructures:
    def test_count_and_to_dict(self):
        data = NewsData(keyword="贵州茅台")
        data.items.append(NewsItem(title="t", source="新浪财经",
                                   url="u", date="d"))
        assert data.count == 1
        d = data.to_dict()
        assert d["keyword"] == "贵州茅台"
        assert d["count"] == 1
        assert "search_trace" in d and "collected_at" in d
