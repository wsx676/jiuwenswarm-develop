# -*- coding: utf-8 -*-
"""事实溯源校验器

对报告中的每条数据与论据进行溯源校验：
1. 是否标注来源（论据卡片级 check / 报告正文级 check_report）
2. 来源是否权威（白名单命中）
3. 引用率 ≥ 90% 闸门（不达标不放行）

事实溯源保障报告严谨性与数据可信度，也是成果可复现性的基础。
"""

import re
from dataclasses import dataclass, field
from typing import List

try:
    from collectors.news_collector import NewsCollector
except ImportError:  # 兼容包导入/直跑：按绝对路径定位技能根目录
    import os
    import sys
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)
    from collectors.news_collector import NewsCollector


@dataclass
class CitationCheck:
    """溯源校验结果"""
    total_claims: int = 0
    cited_claims: int = 0
    authoritative_claims: int = 0
    issues: List[str] = field(default_factory=list)
    min_rate: float = 0.9   # 与 CitationChecker.min_rate 同源（L1 统一）

    @property
    def citation_rate(self) -> float:
        if self.total_claims == 0:
            return 0.0
        return self.cited_claims / self.total_claims

    @property
    def passed(self) -> bool:
        # L1 修复：min_rate 统一口径（此前硬编码 0.9 且要求零 issue，
        # 与 Reviewer 闸门双口径不一致）
        return self.citation_rate >= self.min_rate and len(self.issues) == 0


class CitationChecker:
    """事实溯源校验器"""

    # 权威来源白名单 = 新闻权威媒体白名单 + 财报/行情/宏观数据源
    AUTHORITATIVE_SOURCES = (
        NewsCollector.RELIABLE_SOURCES + [
            "交易所信息披露平台", "巨潮资讯网", "上交所", "深交所",
            "公司定期财报", "公司公告", "国家统计局",
            "akshare", "东方财富", "腾讯财经", "新浪财经", "公开行情数据",
            "组委会公司池",
        ]
    )

    # 报告正文数据句判定：含数字且带计量/比率语义的陈述行
    DATA_LINE_RE = re.compile(
        r"\d+(\.\d+)?\s*(%|亿元|亿|万元|万|元|倍|家|条|只|年|月)")
    # 噪声行：标题/表格/图片引用/列表分隔
    SKIP_LINE_RE = re.compile(r"^(#|\||!\[|---|\*\*|>)")

    def __init__(self, min_rate: float = 0.9):
        self.min_rate = min_rate

    # ------------------------------------------------------------------
    def check(self, claims: List[dict]) -> CitationCheck:
        """论据卡片级校验：每张卡片须有 citation 且命中权威白名单"""
        result = CitationCheck(
            total_claims=len(claims), min_rate=self.min_rate)

        for claim in claims:
            citation = claim.get("citation", "")
            if citation:
                result.cited_claims += 1
                if any(s in citation for s in self.AUTHORITATIVE_SOURCES):
                    result.authoritative_claims += 1
                else:
                    result.issues.append(
                        f"来源非权威: {str(claim.get('text', ''))[:30]}"
                    )
            else:
                result.issues.append(
                    f"论据无来源: {str(claim.get('text', ''))[:30]}"
                )

        return result

    # ------------------------------------------------------------------
    def check_report(self, content: str) -> CitationCheck:
        """报告正文级校验：数据句引用率（所在段落内出现来源标注）

        段落按空行划分；与 ReportWriter「段末换行加数据来源」约定对齐，
        跨段不覆盖（避免隔段误判）；表格/标题/图片行不计入数据句。
        """
        result = CitationCheck(min_rate=self.min_rate)
        lines = content.splitlines()

        # 段落划分：空行为界；段内任意行出现来源标注则整段数据句已引用
        para_of: List[int] = [0] * len(lines)   # 每行所属段号（-1 空行）
        para_has_source: List[bool] = [False]
        pid = 0
        for i, line in enumerate(lines):
            if not line.strip():
                pid += 1
                para_has_source.append(False)
                para_of[i] = -1
                continue
            para_of[i] = pid
            if ("数据来源" in line or "来源：" in line
                    or "（来源" in line):
                para_has_source[pid] = True

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or self.SKIP_LINE_RE.match(stripped):
                continue
            if not self.DATA_LINE_RE.search(stripped):
                continue
            result.total_claims += 1
            if para_has_source[para_of[i]]:
                result.cited_claims += 1
                result.authoritative_claims += 1
            else:
                result.issues.append(
                    f"第 {i + 1} 行数据句所在段落缺少来源标注: "
                    f"{stripped[:40]}")
        return result

    def check_sources_list(self, sources: List[str]) -> List[str]:
        """校验文末来源清单是否全部命中权威白名单，返回非权威项"""
        return [
            s for s in sources
            if not any(w in s for w in self.AUTHORITATIVE_SOURCES)
        ]
