# -*- coding: utf-8 -*-
"""CitationChecker 单元测试：论据卡片校验、报告正文引用率口径、
权威白名单"""

import pytest

from generators.citation_checker import CitationChecker

CLAIMS = [
    {"text": "毛利率 89.6%", "citation": "公司定期财报"},
    {"text": "央行降准释放流动性", "citation": "来源：财联社"},
    {"text": "某自媒体爆料", "citation": "股吧论坛"},   # 非权威
    {"text": "PE 26.96"},                               # 无来源
]


class TestClaims:
    def test_citation_rate_and_issues(self):
        result = CitationChecker().check(CLAIMS)
        assert result.total_claims == 4
        assert result.cited_claims == 3
        assert result.citation_rate == 0.75
        assert len([i for i in result.issues if "非权威" in i]) == 1
        assert len([i for i in result.issues if "无来源" in i]) == 1

    def test_all_authoritative_passes(self):
        ok = CitationChecker().check(CLAIMS[:2])
        assert ok.passed and ok.citation_rate == 1.0

    def test_news_whitelist_aligned(self):
        """白名单与 NewsCollector.RELIABLE_SOURCES 对齐"""
        checker = CitationChecker()
        assert "财联社" in checker.AUTHORITATIVE_SOURCES
        assert "新浪财经" in checker.AUTHORITATIVE_SOURCES

    def test_min_rate_propagates_to_result(self):
        """L1 回归：passed 口径与 min_rate 同源（此前硬编码 0.9，
        自定义阈值下判定与闸门双口径不一致）"""
        result = CitationChecker(min_rate=0.7).check(CLAIMS)
        assert result.min_rate == 0.7
        # rate 0.75 ≥ 0.7，但非权威/无来源 issue 非空仍不通过
        assert not result.passed
        assert len(result.issues) > 0
        ok = CitationChecker(min_rate=0.99).check(CLAIMS[:2])
        assert ok.min_rate == 0.99
        assert ok.passed      # rate 1.0 ≥ 0.99 且零 issue


class TestCheckReport:
    def test_report_citation_rate(self):
        content = "\n".join([
            "# 报告",
            "",
            "公司毛利率 89.6%，ROE 16.8%。",
            "数据来源：公司定期财报",
            "",
            "营收增速 1.3%，未标注来源的数据句。",
        ])
        result = CitationChecker().check_report(content)
        # 数据句按行计：首段一句（段内有来源标注覆盖）、
        # 末段一句（隔段不覆盖，避免隔空行误判）
        assert result.total_claims == 2
        assert result.cited_claims == 1
        assert not result.passed

    def test_table_and_headings_not_counted(self):
        content = "\n".join([
            "## 五、财务分析",
            "| 指标 | 2026-Q2 |",
            "|---|---|",
            "| 毛利率(%) | 89.60 |",
            "",
            "![盈利趋势](charts/margin_600519.png)",
            "*数据来源：公司定期财报*",
        ])
        result = CitationChecker().check_report(content)
        assert result.total_claims == 0

    def test_table_rows_not_counted_paragraph_covered(self):
        """表格行不计入数据句（程序生成同源）；
        正文句在段落内有来源标注则通过"""
        content = "\n".join([
            "| 毛利率(%) | 89.60 |",
            "| ROE(%) | 16.80 |",
            "| 资产负债率(%) | 15.19 |",
            "",
            "最新季度营收 512.5 亿元，同比增长 1.3%。",
            "数据来源：公司定期财报（akshare）",
        ])
        result = CitationChecker().check_report(content)
        assert result.total_claims == 1      # 仅正文句；表格行不计
        assert result.cited_claims == 1
        assert result.passed


class TestSourcesList:
    def test_non_authoritative_flagged(self):
        bad = CitationChecker().check_sources_list(
            ["公司定期财报", "股吧论坛", "新浪财经"])
        assert bad == ["股吧论坛"]
