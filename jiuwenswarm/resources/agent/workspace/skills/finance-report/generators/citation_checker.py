# -*- coding: utf-8 -*-
"""事实溯源校验器

对报告中的每条数据与论据进行溯源校验：
1. 是否标注来源
2. 来源是否权威
3. 数据值是否与来源一致

事实溯源保障报告严谨性与数据可信度，也是成果可复现性的基础。
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class CitationCheck:
    """溯源校验结果"""
    total_claims: int = 0
    cited_claims: int = 0
    authoritative_claims: int = 0
    issues: List[str] = field(default_factory=list)

    @property
    def citation_rate(self) -> float:
        if self.total_claims == 0:
            return 0.0
        return self.cited_claims / self.total_claims

    @property
    def passed(self) -> bool:
        return self.citation_rate >= 0.9 and len(self.issues) == 0


class CitationChecker:
    """事实溯源校验器"""

    AUTHORITATIVE_SOURCES = [
        "国家统计局", "上交所", "深交所",
        "巨潮资讯网", "新浪财经", "财联社", "证券时报",
    ]

    def check(self, claims: List[dict]) -> CitationCheck:
        result = CitationCheck(total_claims=len(claims))

        for claim in claims:
            citation = claim.get("citation", "")
            if citation:
                result.cited_claims += 1
                if any(s in citation for s in self.AUTHORITATIVE_SOURCES):
                    result.authoritative_claims += 1
                else:
                    result.issues.append(
                        f"来源非权威: {claim.get('text', '')[:30]}"
                    )
            else:
                result.issues.append(
                    f"论据无来源: {claim.get('text', '')[:30]}"
                )

        return result
