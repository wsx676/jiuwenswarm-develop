# -*- coding: utf-8 -*-
"""审查校验 Agent

执行四类检查：
1. 事实溯源校验：论据卡片引用 + 正文数据句引用率 ≥ 90% 闸门
   （统一 CitationChecker 口径，与提交验收一致）
2. 图文一致性：图表数据与正文数据是否一致
3. 结构完整性：必要章节是否齐全
4. 合规性校验：风险提示/免责声明/数据来源标注
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

try:
    from generators.citation_checker import CitationChecker
except ImportError:  # 兼容包导入/直跑：按绝对路径定位技能根目录
    import os
    import sys
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)
    from generators.citation_checker import CitationChecker

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """审查结果"""
    passed: bool
    score: float               # 0-100
    notes: str                 # 审查意见
    issues: List[str]          # 问题清单
    feedback: dict             # 反馈给上游的修改建议


class ReviewerAgent:
    """审查校验 Agent"""

    # 权威数据源白名单（用于溯源校验）
    AUTHORITATIVE_SOURCES = [
        "国家统计局", "上交所", "深交所",
        "巨潮资讯网", "新浪财经", "财联社", "证券时报",
        "东方财富", "同花顺",
    ]

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def review(self, draft, research_data) -> ReviewResult:
        issues = []

        # 1. 事实溯源校验
        citation_issues = self._check_citations(draft)
        issues.extend(citation_issues)

        # 2. 图文一致性校验
        consistency_issues = self._check_chart_text_consistency(draft)
        issues.extend(consistency_issues)

        # 3. 结构完整性校验
        structure_issues = self._check_structure(draft)
        issues.extend(structure_issues)

        # 4. 合规性校验（风险提示等）
        compliance_issues = self._check_compliance(draft)
        issues.extend(compliance_issues)

        score = max(0.0, 100.0 - len(issues) * 10.0)
        passed = len(issues) == 0 and score >= 70.0

        return ReviewResult(
            passed=passed,
            score=score,
            notes=f"审查得分 {score:.1f}，发现 {len(issues)} 个问题",
            issues=issues,
            feedback={"issues": issues, "research_data": research_data},
        )

    def _check_citations(self, draft) -> List[str]:
        """事实溯源校验：论据卡片引用 + 正文数据句引用率闸门

        与 CitationChecker 统一口径（claims 为 {text, citation} 字典）。
        """
        issues = []
        checker = CitationChecker()
        claims = getattr(draft, "claims", []) or []
        if claims:
            issues.extend(checker.check(claims).issues)
        # 正文数据句引用率 ≥ 90% 闸门（不达标不救，回流重写）
        report = checker.check_report(getattr(draft, "content", "") or "")
        if report.total_claims and report.citation_rate < checker.min_rate:
            issues.append(
                f"正文数据句引用率 {report.citation_rate:.0%} "
                f"低于 {checker.min_rate:.0%} 闸门"
                f"（{len(report.issues)} 处缺来源标注）"
            )
        return issues

    def _check_chart_text_consistency(self, draft) -> List[str]:
        """图文一致性校验"""
        issues = []
        for chart in getattr(draft, "charts", []) or []:
            for mention in getattr(chart, "text_mentions", []) or []:
                if abs(mention - chart.data_value) > 0.01:
                    issues.append(
                        f"图文不一致: {chart.title} "
                        f"图表值={chart.data_value} 正文值={mention}"
                    )
        return issues

    def _check_structure(self, draft) -> List[str]:
        """结构完整性校验"""
        issues = []
        required_sections = [
            "核心观点", "投资结论", "财务分析", "估值分析", "风险提示",
        ]
        for section in required_sections:
            if section not in draft.content:
                issues.append(f"缺失必要章节: {section}")
        return issues

    def _check_compliance(self, draft) -> List[str]:
        """合规性校验"""
        issues = []
        if "风险提示" not in draft.content:
            issues.append("缺失风险提示章节")
        if "免责声明" not in draft.content:
            issues.append("缺失免责声明")
        if "数据来源" not in draft.content:
            issues.append("缺失数据来源标注")
        return issues
