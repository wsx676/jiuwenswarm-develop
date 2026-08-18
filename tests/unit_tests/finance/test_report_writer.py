# -*- coding: utf-8 -*-
"""ReportWriter 单元测试：LLM 降级路径下的八段结构、评级规则、
图表程序化注入、图片本地化校验、来源清单与论据卡片（纯本地）"""

from types import SimpleNamespace

import pytest

from analyzers.finance_analyzer import FinanceAnalysis
from analyzers.industry_analyzer import IndustryAnalysis
from generators.chart_generator import Chart
from generators.report_writer import ReportWriter


def make_writer() -> ReportWriter:
    """LLM 置为不可用：全流程走规则降级（离线可跑）"""
    writer = ReportWriter()
    writer._llm_ready = True   # 跳过懒加载，_llm 保持 None
    writer._llm = None
    return writer


@pytest.fixture
def request_():
    return SimpleNamespace(report_type="company", name="贵州茅台",
                           target="600519", period="2026-08-17")


@pytest.fixture
def data():
    finance = FinanceAnalysis(
        profitability={"gross_margin": 89.6},
        valuation={"pe": 26.96},
        insights=["毛利率 89.6% 保持高位", "ROE 16.8% 盈利能力突出",
                  "货币资金充裕", "营收增速 1.3%"],
    )
    industry = IndustryAnalysis(
        sector="消费板块",
        prosperity={"level": "景气向上", "sentiment_score": 65,
                    "news_count": 5, "positive_hits": 3,
                    "negative_hits": 0, "policy_signals": ["促消费"]},
        competition={"companies": ["贵州茅台", "五粮液"],
                     "table": [], "target_rank": {"gross_margin": 1},
                     "leader_metrics": ["毛利率(%)"]},
        peers=[("000858", "五粮液")],
        insights=["「消费板块」板块景气度判定为景气向上",
                  "标的在 毛利率(%) 上居板块首位"],
    )
    return {
        "quote_data": {"name": "贵州茅台", "source": "腾讯财经日线",
                       "latest_close": 1286.09, "period_return": 2.35,
                       "collected_at": "2026-08-17"},
        "filing_data": {"source": "akshare 财务摘要",
                        "collected_at": "2026-08-17",
                        "statements": [{"period": "2026-Q2"}]},
        "news_data": {"items": [
            {"title": "白酒促消费政策落地", "source": "财联社",
             "date": "2026-08-16", "summary": ""}]},
        "finance_analysis": finance,
        "industry_analysis": industry,
    }


class TestStructure:
    def test_eight_sections_and_disclaimer(self, data, request_):
        draft = make_writer().write(data, request_)
        for title in ("一、核心观点", "二、投资结论与仓位建议",
                      "三、公司概况", "四、行业分析", "五、财务分析",
                      "六、估值分析", "七、风险提示", "八、数据来源"):
            assert f"## {title}" in draft.content
        assert "免责声明" in draft.content
        assert len(draft.outline) == 8          # 降级固定八段大纲
        # 降级模板段落带来源标注（引用闸门基础：三/四/五/六章节）
        assert draft.content.count("数据来源：") >= 4

    def test_non_company_report_placeholder(self):
        req = SimpleNamespace(report_type="industry", name="白酒",
                              target="600519", period="2026-08-17")
        draft = make_writer().write({}, req)
        assert "待生成" in draft.content


class TestRating:
    def test_positive_signals_lead_to_overweight(self, data, request_):
        draft = make_writer().write(data, request_)
        assert "增持" in draft.content
        assert "5%-10%" in draft.content

    def test_negative_hints_lead_to_reduce(self, request_):
        finance = FinanceAnalysis(
            insights=["营收增速承压", "净利润下滑", "ROE 偏弱待观察"])
        data = {"quote_data": {}, "filing_data": {"statements": []},
                "news_data": {"items": []},
                "finance_analysis": finance}
        draft = make_writer().write(data, request_)
        assert "减持观望" in draft.content
        assert "0%" in draft.content


class TestCharts:
    def test_charts_injected_after_sections(self, data, request_,
                                            tmp_path):
        png = tmp_path / "price_600519.png"
        png.write_bytes(b"\x89PNG")
        bar = tmp_path / "margin_600519.png"
        bar.write_bytes(b"\x89PNG")
        data["charts"] = [
            Chart("股价走势", "line", {}, str(png), "图注", "腾讯财经日线"),
            Chart("盈利趋势", "bar", {}, str(bar), "图注", "公司定期财报"),
        ]
        content = make_writer().write(data, request_).content
        # line 随公司概况、bar 随财务分析（程序化注入）
        assert (content.index("## 三、公司概况")
                < content.index("![股价走势]")
                < content.index("## 四、行业分析"))
        assert content.index("![盈利趋势]") > content.index(
            "## 五、财务分析")

    def test_missing_image_replaced_and_logged(self, data, request_):
        data["charts"] = [
            Chart("股价走势", "line", {},
                  r"C:\nonexistent\price_600519.png", "", ""),
        ]
        draft = make_writer().write(data, request_)
        assert "![股价走势]" not in draft.content
        assert "生成失败" in draft.content
        assert data["image_issues"]               # 留痕给 Reviewer

    def test_external_link_image_removed(self, data, request_):
        data["charts"] = [
            Chart("外部图", "line", {}, "http://example.com/x.png", "", ""),
        ]
        content = make_writer().write(data, request_).content
        assert "外部链接" in content
        assert "![外部图]" not in content


class TestNormalizeSection:
    def test_strips_duplicate_title_and_merges_blank_lines(self):
        text = ("## 五、财务分析\n\n五、财务分析\n\n"
                "毛利率 89.6% 保持高位。\n\n"
                "数据来源：公司定期财报")
        out = ReportWriter._normalize_section("五、财务分析", text)
        assert out.startswith("毛利率")      # 重复标题行（含 # 变体）剥离
        assert "\n\n" not in out          # 段内空行合并（章末来源覆盖全章）
        assert "数据来源：公司定期财报" in out

    def test_filters_placeholder_echo(self):
        """M3 回归：LLM 原样复述 prompt 占位符「本文首段」时整行剔除"""
        text = "本文首段\n营收 500 亿元。\n数据来源：公司定期财报"
        out = ReportWriter._normalize_section("一、核心观点", text)
        assert "本文首段" not in out
        assert "营收 500 亿元。" in out


class TestSourcesAndClaims:
    def test_sources_list_and_claims(self, data, request_):
        draft = make_writer().write(data, request_)
        assert any("行情数据：腾讯财经日线" in s for s in draft.citations)
        assert any("财务数据：akshare 财务摘要" in s
                   for s in draft.citations)
        assert any("新闻资讯：财联社" in s for s in draft.citations)
        assert {"text": "毛利率 89.6% 保持高位",
                "citation": "公司定期财报"} in draft.claims
