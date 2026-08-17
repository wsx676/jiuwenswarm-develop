# -*- coding: utf-8 -*-
"""MacroAnalyzer 单元测试：指标提取（mock akshare）、
政策关键词规则、板块影响映射、接口降级（无网络）"""

from unittest.mock import MagicMock

import pytest

from analyzers.macro_analyzer import MacroAnalyzer


def _mock_akshare(monkeypatch, gdp=5.1, cpi=0.3, pmi=50.4):
    """注入假 akshare：GDP/CPI/PMI 各返回**多期乱序** DataFrame

    乱序设计：末行非最新期（复现 C1——接口新→旧排序下 iloc[-1]
    误取最旧一期）；PMI 带「非制造业-同比增长」干扰列（锁定
    关键词匹配命中真实列「制造业-指数」而非兜底取错列）。
    """
    import pandas as pd
    import sys

    ak = MagicMock()
    ak.macro_china_gdp = MagicMock(return_value=pd.DataFrame([
        {"季度": "2026-06-30", "国内生产总值": 320000.0,
         "同比增长-不变价": gdp},
        {"季度": "2025-12-31", "国内生产总值": 310000.0,
         "同比增长-不变价": gdp - 0.2},
        {"季度": "2025-06-30", "国内生产总值": 300000.0,
         "同比增长-不变价": gdp - 0.4},
    ]))
    ak.macro_china_cpi = MagicMock(return_value=pd.DataFrame([
        {"月份": "2026-07", "全国-同比增长": cpi},
        {"月份": "2008-01", "全国-同比增长": cpi + 6.7},   # 历史高值干扰
        {"月份": "2026-06", "全国-同比增长": cpi - 0.1},
    ]))
    ak.macro_china_pmi = MagicMock(return_value=pd.DataFrame([
        {"月份": "2026-07", "制造业-指数": pmi,
         "非制造业-同比增长": -0.33},
        {"月份": "2008-01", "制造业-指数": pmi - 1.0,
         "非制造业-同比增长": -0.33},
        {"月份": "2026-06", "制造业-指数": pmi + 0.2,
         "非制造业-同比增长": 0.5},
    ]))
    monkeypatch.setitem(sys.modules, "akshare", ak)
    return ak


class TestIndicators:
    def test_indicator_extraction_with_period_and_source(self, monkeypatch):
        _mock_akshare(monkeypatch)
        result = MacroAnalyzer().analyze()
        # 乱序下必须取到最新期（非接口行序末行的最旧期）
        assert result.indicators["GDP"]["value"] == 5.1
        assert result.indicators["GDP"]["period"] == "2026-06-30"
        assert result.indicators["CPI"]["period"] == "2026-07"
        assert result.indicators["PMI"]["value"] == 50.4
        # PMI 命中真实列「制造业-指数」，而非兜底误取干扰列
        assert result.indicators["PMI"]["column"] == "制造业-指数"
        assert "国家统计局" in result.indicators["PMI"]["source"]
        assert any("GDP 最新值 5.1" in i for i in result.insights)

    def test_out_of_range_value_skipped(self, monkeypatch):
        """C1 回归：值越出健全区间视为取错列，降级跳过不进报告
        （复现历史错值 7.08 混入 PMI 位的场景）"""
        import sys
        import pandas as pd
        ak = MagicMock()
        ak.macro_china_pmi = MagicMock(return_value=pd.DataFrame(
            [{"月份": "2026-07", "制造业-指数": 7.08}]))
        ak.macro_china_gdp = MagicMock(side_effect=RuntimeError)
        ak.macro_china_cpi = MagicMock(side_effect=RuntimeError)
        monkeypatch.setitem(sys.modules, "akshare", ak)
        result = MacroAnalyzer().analyze()
        assert "PMI" not in result.indicators

    def test_indicator_failure_degrades_to_policy_only(self, monkeypatch):
        import sys
        ak = MagicMock()
        ak.macro_china_gdp = MagicMock(
            side_effect=RuntimeError("接口不可用"))
        ak.macro_china_cpi = MagicMock(side_effect=RuntimeError)
        ak.macro_china_pmi = MagicMock(side_effect=RuntimeError)
        monkeypatch.setitem(sys.modules, "akshare", ak)
        result = MacroAnalyzer().analyze()
        assert result.indicators == {}
        assert any("以政策趋势为主" in i for i in result.insights)


class TestPolicyTrends:
    def test_easing_and_active_fiscal_detected(self):
        news = {"items": [
            {"title": "央行降准释放流动性", "summary": "降息预期升温",
             "source": "财联社"},
            {"title": "专项债提速，促消费政策加码", "summary": "以旧换新",
             "source": "新华社"},
        ]}
        result = MacroAnalyzer().analyze(news)
        assert result.policy_trends["monetary"]["direction"] == "宽松"
        assert "降准" in result.policy_trends["monetary"]["signals"]
        assert result.policy_trends["fiscal"]["direction"] == "积极"

    def test_no_policy_news_is_neutral(self):
        result = MacroAnalyzer().analyze({"items": []})
        assert result.policy_trends["monetary"]["direction"] == "中性"


class TestSectorImpact:
    def test_pmi_expansion_and_consumption_policy(self, monkeypatch):
        _mock_akshare(monkeypatch, pmi=51.2, cpi=0.8)
        result = MacroAnalyzer().analyze({"items": [
            {"title": "专项债提速，促消费政策加码", "summary": "",
             "source": "新华社"}]})
        impact = result.sector_impact
        assert "促消费" in impact["消费"]          # 积极财政利好消费
        assert "扩张" in impact["周期/资源"]        # PMI ≥ 50
        assert "扩张" in impact["高端制造/基建"]
        assert set(impact) == {
            "消费", "金融", "新能源/电力", "科技/AI/半导体",
            "周期/资源", "高端制造/基建"}
