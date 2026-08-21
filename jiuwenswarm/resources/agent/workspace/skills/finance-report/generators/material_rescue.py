# -*- coding: utf-8 -*-
"""材料三维评估与补救循环（优化方案 3，来源：得物检索 Agent 实践）

问题：材料缺失时只能写降级模板段（内容空洞），评审会看到"数据不足"
的痕迹却不知为何不足。

设计（report_writer 撰写前、每章材料执行，纯规则判定不走 LLM）：
- 相关性：材料中目标实体名（公司名/板块名）出现次数，0 次即不足
- 完整性：该章必需字段是否非空（如景气度分析须有景气结论+新闻统计）
- 时效性：新闻日期是否超过阈值（默认 90 天），过期仅提示不阻断

不足时按"指南针而非地图"触发一次补救（每章最多一轮）：
① RAG 精读：拉方法论文档全文而非片段，注入写作参考；
② 缓存补采：必需字段缺失时从 research_data 已有采集数据补建；
仍不足才降级，并在材料中标注"数据缺失原因"——把"降级"变成
"可解释的决策"（原因随材料注入 LLM/模板段，写入正文）。

确定性承诺：总开关 material_rescue.enabled 默认 False，关闭时
材料组装与旧口径逐字节一致；评估统计累计 RUN_STATS 留痕。
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

try:
    from common.telemetry import RUN_STATS
except ImportError:  # 兼容包导入/直跑：按绝对路径定位技能根目录
    import os
    import sys
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)
    from common.telemetry import RUN_STATS

logger = logging.getLogger(__name__)

# 必需字段表：研报类型 → 章节标题关键词 → 取值路径列表
# 取值路径："a.b" 表示 payload[a][b] 真值；只列材料缺失即降级的关键章，
# 保守从严，避免误判注入多余"缺失原因"
REQUIRED_FIELDS = {
    "company": {
        "公司概况": ["近期新闻标题"],
        "行业分析": ["景气度.level"],
        "财务分析": ["财务指标"],
        "估值": ["估值指标"],
    },
    "industry": {
        "行业概况": ["成分公司"],
        "景气度": ["景气度统计.level", "景气度统计.news_count"],
        "竞争格局": ["对比表"],
    },
    "macro": {
        "宏观指标": ["指标明细"],
        "政策": ["政策趋势"],
    },
}

# 新闻日期格式（NewsCollector 各源口径）
_DATE_RE = re.compile(
    r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{8})")


@dataclass
class AssessResult:
    """单章材料三维评估结果"""
    ok: bool = True
    missing: List[str] = field(default_factory=list)   # 缺失必需字段
    stale_note: str = ""                               # 时效提示（不阻断）
    entity_hits: int = 0
    rescued_by: List[str] = field(default_factory=list)


def _parse_date(text) -> Optional[date]:
    """解析材料中的日期文本；无法解析返回 None（保守不判过期）"""
    m = _DATE_RE.search(str(text or ""))
    if not m:
        return None
    s = m.group(1).replace("年", "-").replace("月", "-").replace(
        "日", "").replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _latest_news_date(news: dict) -> Optional[date]:
    """新闻缓存中最新一条的日期（无日期条目返回 None）"""
    dates = [d for d in (_parse_date(it.get("date"))
                         for it in (news.get("items") or [])) if d]
    return max(dates) if dates else None


class MaterialRescue:
    """材料三维评估与补救（config: material_rescue.* 可调）"""

    def __init__(self, config: Optional[dict] = None,
                 entities: Optional[List[str]] = None, stats=None):
        cfg = (config or {}).get("material_rescue") or {}
        # 总开关默认关：复现场景保持与旧口径逐字节一致
        self.enabled = bool(cfg.get("enabled", False))
        self.stale_days = int(cfg.get("stale_days", 90))
        self.entities = [e for e in (entities or []) if e]
        self.stats = stats or RUN_STATS

    # ------------------------------------------------------------------
    def assess(self, title: str, payload: dict, report_type: str,
               news: Optional[dict] = None) -> AssessResult:
        """三维评估：相关性 + 完整性 + 时效性（纯规则，不走 LLM）"""
        res = AssessResult()
        # 相关性：目标实体名出现次数（0 次 = 材料未锚定研究对象）
        text = str(payload)
        res.entity_hits = sum(text.count(e) for e in self.entities)
        if self.entities and res.entity_hits == 0:
            res.ok = False
            res.missing.append("目标实体未提及")
        # 完整性：该章必需字段非空
        for path in self._required_paths(report_type, title):
            if not self._truthy_at(payload, path):
                res.ok = False
                res.missing.append(path.split(".")[-1] or path)
        # 时效性：新闻日期超阈值仅提示（旧数据仍优于无数据，不阻断）
        latest = _latest_news_date(news or {})
        if latest:
            age = (date.today() - latest).days
            if age > self.stale_days:
                res.stale_note = (
                    f"最新新闻日期距今 {age} 天"
                    f"（>{self.stale_days} 天阈值）")
        return res

    def rescue(self, title: str, payload: dict, data: dict,
               report_type: str, result: AssessResult) -> dict:
        """补救循环（一次）：RAG 精读 + 缓存补采；仍不足标注缺失原因

        返回新 payload（不改原始 dict）。补救手段按缺失类型定向触发，
        而非无差别堆料——"指南针而非地图"。遥测留痕 RUN_STATS：
        failed/rescued/degraded 计数（答辩可解释补救决策）。
        """
        initial_ok = result.ok
        payload = dict(payload)
        # ① 缓存补采：必需字段缺失 → 从 research_data 已有数据补建
        for path in self._required_paths(report_type, title):
            if self._truthy_at(payload, path):
                continue
            value = self._from_cache(title, path, data)
            if value:
                self._set_at(payload, path, value)
                result.rescued_by.append(f"缓存补采:{path.split('.')[-1]}")
        # ② RAG 精读：仅评估失败时拉方法论文档全文（非片段）注入参考；
        #    时效提示类不触发（材料本身齐全，只是偏旧）
        chunks = data.get("knowledge_chunks") or []
        if not initial_ok and chunks and "方法论精读" not in payload:
            full = "\n".join(str(c.get("content", ""))[:400]
                             for c in chunks[:2])
            payload["方法论精读（知识库全文，仅作方法论参考）"] = full
            result.rescued_by.append("RAG精读")
        # ③ 实体锚定缺失 → 显式注入研究对象（防 LLM 跑题）
        if "目标实体未提及" in result.missing and self.entities:
            payload["研究对象"] = self.entities[0]
            result.rescued_by.append("实体锚定")
        # 复评：仍不足 → 标注数据缺失原因（降级变成可解释的决策）
        still = self.assess(title, payload, report_type,
                            data.get("news_data"))
        if not still.ok:
            reasons = "、".join(sorted(set(still.missing)))
            payload["数据缺失原因"] = (
                f"以下关键材料缺失：{reasons}。已执行补救："
                f"{'、'.join(result.rescued_by) or '无可补救来源'}；"
                "本章基于现有可得材料撰写，结论以材料为准")
            result.missing = still.missing
        if not initial_ok:
            self.stats.add_material_rescue(rescued=still.ok)
        result.ok = still.ok
        if still.stale_note:
            payload["数据时效提示"] = still.stale_note
            result.stale_note = still.stale_note
        return payload

    # ------------------------------------------------------------------
    def _required_paths(self, report_type: str, title: str) -> List[str]:
        for key, paths in REQUIRED_FIELDS.get(report_type, {}).items():
            if key in title:
                return paths
        return []

    @staticmethod
    def _truthy_at(payload: dict, path: str) -> bool:
        node = payload
        for part in path.split("."):
            if not isinstance(node, dict) or not node.get(part):
                return False
            node = node[part]
        return bool(node)

    @staticmethod
    def _set_at(payload: dict, path: str, value) -> None:
        parts = path.split(".")
        node = payload
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    @staticmethod
    def _from_cache(title: str, path: str, data: dict):
        """缓存补采：按缺失字段定向从 research_data 已有数据补建"""
        field_name = path.split(".")[-1]
        news = data.get("news_data") or {}
        if field_name in ("近期新闻标题", "政策信号新闻") \
                and news.get("items"):
            return [f"{it.get('title', '')}（{it.get('source', '')}，"
                    f"{it.get('date', '')}）"
                    for it in news["items"][:8]]
        industry = data.get("industry_analysis")
        if field_name in ("level", "news_count", "sentiment_score") \
                and industry is not None:
            prosperity = (industry.to_dict() or {}).get("prosperity", {})
            return prosperity.get(field_name)
        if field_name == "成分公司" and industry is not None:
            return (industry.to_dict() or {}).get("peers", [])
        return None
