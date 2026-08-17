# -*- coding: utf-8 -*-
"""IndustryAnalyzer 单元测试：板块归属、竞对两两对比、
景气度情绪规则、缺失数据降级（纯本地，无网络）"""

import pytest

from analyzers.industry_analyzer import IndustryAnalyzer

POOL = {
    "消费板块": [("600519", "贵州茅台"), ("000858", "五粮液"),
                 ("600809", "山西汾酒")],
    "金融板块": [("601398", "工商银行")],
}
PEER_METRICS = {
    "600519": {"name": "贵州茅台", "revenue": 500.0, "net_profit": 250.0,
               "gross_margin": 89.6, "net_margin": 50.8, "roe": 16.8},
    "000858": {"name": "五粮液", "revenue": 400.0, "net_profit": 150.0,
               "gross_margin": 75.0, "net_margin": 37.0, "roe": 22.0},
    "600809": {"name": "山西汾酒", "revenue": 300.0, "net_profit": 100.0,
               "gross_margin": 70.0, "net_margin": 33.0, "roe": 30.0},
}


@pytest.fixture
def analyzer():
    return IndustryAnalyzer()


class TestSectorAndPeers:
    def test_sector_and_peers_from_pool(self, analyzer):
        result = analyzer.analyze("600519", POOL,
                                  peer_metrics=PEER_METRICS)
        assert result.sector == "消费板块"
        assert result.peers == [("000858", "五粮液"),
                                ("600809", "山西汾酒")]

    def test_symbol_not_in_pool(self, analyzer):
        result = analyzer.analyze("999999", POOL)
        assert result.sector == ""
        assert any("公司池" in i for i in result.insights)


class TestCompetition:
    def test_pairwise_comparison_and_rank(self, analyzer):
        result = analyzer.analyze("600519", POOL,
                                  peer_metrics=PEER_METRICS)
        comp = result.competition
        assert len(comp["companies"]) == 3
        assert comp["target_rank"]["gross_margin"] == 1   # 茅台毛利率第一
        assert comp["target_rank"]["roe"] == 3            # ROE 板块第三
        assert "毛利率(%)" in comp["leader_metrics"]
        # 表：表头 + 5 个指标行
        assert len(comp["table"]) == 6
        assert comp["table"][0][1] == "贵州茅台"

    def test_missing_peer_metric_skipped_in_rank(self, analyzer):
        metrics = dict(PEER_METRICS)
        metrics["000858"] = {"name": "五粮液", "roe": 22.0}  # 其余 None
        result = analyzer.analyze("600519", POOL, peer_metrics=metrics)
        rank = result.competition["target_rank"]
        assert rank["roe"] == 3  # 汾酒 30 > 五粮液 22 > 茅台 16.8，标的第三
        # 五粮液 revenue 缺失被剔除，茅台 500 > 汾酒 300 居首
        assert rank["revenue"] == 1

    def test_no_peer_metrics_degrades(self, analyzer):
        result = analyzer.analyze("600519", POOL)
        assert result.competition["table"] == []
        assert "数据不足" in result.competition.get("note", "")
        assert any("2 家" in i for i in result.insights)


class TestProsperity:
    def test_sentiment_level_positive(self, analyzer):
        news = {"items": [
            {"title": "白酒促消费政策落地，行业复苏加速",
             "summary": "多家酒企提价，业绩超预期", "source": "财联社"},
            {"title": "高端白酒销量创新高", "summary": "", "source": "新华网"},
        ]}
        result = analyzer.analyze("600519", POOL, news_data=news,
                                  peer_metrics=PEER_METRICS)
        pros = result.prosperity
        assert pros["news_count"] == 2
        assert pros["positive_hits"] >= 3
        assert pros["level"] in ("景气向上", "平稳运行")
        assert "促消费" in pros["policy_signals"]

    def test_no_news_marks_insufficient(self, analyzer):
        result = analyzer.analyze("600519", POOL)
        assert result.prosperity["level"] == "数据不足"
