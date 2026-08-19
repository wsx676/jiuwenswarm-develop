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
import re
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

    # 结构校验必需章节集：按研报类型区分（行业/宏观无财务/估值章）
    REQUIRED_SECTIONS = {
        "company": ["核心观点", "投资结论", "财务分析", "估值分析", "风险提示"],
        "industry": ["核心观点", "投资结论", "竞争格局", "风险提示"],
        "macro": ["核心观点", "宏观结论", "风险提示"],
    }

    def review(self, draft, research_data) -> ReviewResult:
        issues = []

        # 1. 事实溯源校验
        citation_issues = self._check_citations(draft)
        issues.extend(citation_issues)

        # 2. 图文一致性校验
        consistency_issues = self._check_chart_text_consistency(draft)
        issues.extend(consistency_issues)

        # 3. 结构完整性校验（必需章节集按研报类型区分）
        report_type = (research_data or {}).get("report_type", "company")
        structure_issues = self._check_structure(draft, report_type)
        issues.extend(structure_issues)

        # 4. 合规性校验（风险提示等）
        compliance_issues = self._check_compliance(draft)
        issues.extend(compliance_issues)

        # L2 修复：issues==0 时 score 恒为 100，阈值条件冗余
        score = max(0.0, 100.0 - len(issues) * 10.0)
        passed = len(issues) == 0

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
        """图文一致性校验：图表关键数值须在正文出现且无矛盾数值

        M1 修复：原实现读 Chart 不存在的字段（恒通过的死代码）；
        现基于真实字段：抽取 chart.data 标量数值，正文须存在
        近似值（相对误差 ≤ 2%）；表格型图表 caption 即正文表格，跳过。
        """
        issues = []
        content = getattr(draft, "content", "") or ""
        body_nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", content)]
        for chart in getattr(draft, "charts", []) or []:
            if getattr(chart, "chart_type", "") == "table":
                continue
            data = getattr(chart, "data", None) or {}
            for key in ("latest_close", "period_return"):
                value = data.get(key)
                if not isinstance(value, (int, float)):
                    continue
                if not any(abs(value - n) <= max(abs(value) * 0.02, 0.005)
                           for n in body_nums):
                    issues.append(
                        f"图文不一致: {chart.title} 的 {key}={value} "
                        f"未在正文出现（或被改写）"
                    )
        return issues

    def _check_structure(self, draft, report_type: str = "company") -> List[str]:
        """结构完整性校验（必需章节集按研报类型区分）"""
        issues = []
        required_sections = self.REQUIRED_SECTIONS.get(
            report_type, self.REQUIRED_SECTIONS["company"])
        for section in required_sections:
            if section not in draft.content:
                issues.append(f"缺失必要章节: {section}")
        return issues

    def _check_compliance(self, draft) -> List[str]:
        """合规性校验（L2 修复：风险提示已由 _check_structure 校验，
        此处不再重复计分）"""
        issues = []
        if "免责声明" not in draft.content:
            issues.append("缺失免责声明")
        if "数据来源" not in draft.content:
            issues.append("缺失数据来源标注")
        return issues
