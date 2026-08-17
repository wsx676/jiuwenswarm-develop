# -*- coding: utf-8 -*-
"""混合记忆管理

公司池数十家标的、数据源多、单标的分析链路长，全量数据进上下文必然
记忆爆炸。三层混合记忆系统分流管理（对应开发实践 5.5）：

- 短期记忆（LLM 上下文窗口）：当前标的分析上下文；大表格只保留
  表头 + 前 5 行 + 后 5 行，预算超限按 FIFO 逐出最旧条目并留痕
- 长期记忆（持久化 JSON）：每家公司分析完成后将结论与关键信息
  摘要沉淀，跨标的分析时以摘要形态注入后续标的上下文
- 外部记忆（RAG 向量知识库）：财务方法论 / 行业框架 / 历史结论按需检索

分流规则（ingest）：表格类数据只以压缩预览入短期；结论/事实类信息
同时进短期（当轮使用）并沉淀长期（后续复用）。
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 大表格压缩：表头 + 前 5 行 + 后 5 行
HEAD_ROWS = 5
TAIL_ROWS = 5


def compress_table(rows: List[List], head: int = HEAD_ROWS,
                   tail: int = TAIL_ROWS, sep: str = " | ") -> str:
    """大表格压缩为文本预览：首行视为表头，保留前 head 行与后 tail 行

    行数未超过 head + tail 时原样输出（无压缩）。
    """
    if not rows:
        return ""
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    if len(cells) <= head + tail:
        return "\n".join(sep.join(r) for r in cells)
    kept = cells[:head] + cells[-tail:]
    omitted = len(cells) - head - tail
    lines = [sep.join(r) for r in kept[:head]]
    lines.append(f"…（中间省略 {omitted} 行，共 {len(cells)} 行）…")
    lines += [sep.join(r) for r in kept[head:]]
    return "\n".join(lines)


@dataclass
class CompanySummary:
    """单标的长期记忆：分析结论摘要沉淀"""
    symbol: str
    name: str = ""
    sector: str = ""
    conclusion: str = ""          # 核心结论（评级/观点）
    key_metrics: Dict[str, float] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)
    updated_at: str = ""

    def as_text(self, max_chars: int = 300) -> str:
        """摘要形态文本（跨标的注入上下文用，限长）"""
        parts = [f"[{self.symbol} {self.name}]".strip()]
        if self.sector:
            parts.append(f"板块：{self.sector}")
        if self.conclusion:
            parts.append(self.conclusion)
        if self.key_metrics:
            metrics = "；".join(f"{k}={v}" for k, v in self.key_metrics.items())
            parts.append(f"关键指标：{metrics}")
        parts.extend(self.insights[:2])
        text = "；".join(p for p in parts if p)
        return text[:max_chars] + ("…" if len(text) > max_chars else "")

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "name": self.name, "sector": self.sector,
            "conclusion": self.conclusion, "key_metrics": self.key_metrics,
            "insights": self.insights, "updated_at": self.updated_at,
        }


class ShortTermMemory:
    """短期记忆：当前标的上下文窗口（字符预算 + FIFO 逐出）"""

    def __init__(self, budget: int = 12000):
        self.budget = budget
        self._sections: List[dict] = []
        self.evicted = 0  # 逐出计数（留痕：批量运行记忆分流的证据）

    def add(self, key: str, content: str) -> bool:
        """添加段落；返回是否发生逐出。同 key 覆盖旧内容"""
        content = content.strip()
        if not content:
            return False
        self._sections = [s for s in self._sections if s["key"] != key]
        self._sections.append({"key": key, "content": content})
        dropped = False
        # 新段已计入 size，超预算则逐出最旧段落（保留至少当前段）
        while self.size > self.budget and len(self._sections) > 1:
            old = self._sections.pop(0)
            self.evicted += 1
            dropped = True
            logger.info("短期记忆逐出：%s（%d 字符），为 %s 腾出预算",
                        old["key"], len(old["content"]), key)
        return dropped

    @property
    def size(self) -> int:
        return sum(len(s["content"]) for s in self._sections)

    @property
    def keys(self) -> List[str]:
        return [s["key"] for s in self._sections]

    def render(self) -> str:
        """拼接为上下文文本（注入 LLM prompt）"""
        return "\n\n".join(f"## {s['key']}\n{s['content']}"
                           for s in self._sections)

    def clear(self):
        """切换标的时清空（逐出计数保留，作为整轮运行的留痕）"""
        self._sections = []


class LongTermMemory:
    """长期记忆：公司分析结论摘要的持久化沉淀（JSON，按 symbol 去重更新）"""

    def __init__(self, store_path: str):
        self.store_path = store_path
        self._cache: Optional[Dict[str, dict]] = None

    # ------------------------------------------------------------------
    def save_summary(self, summary: CompanySummary):
        """沉淀/更新单标的结论摘要（幂等：同 symbol 覆盖）"""
        store = self._load()
        if not summary.updated_at:
            summary.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        store[summary.symbol] = summary.to_dict()
        self._save(store)
        logger.info("长期记忆沉淀：%s(%s) 结论摘要", summary.symbol,
                    summary.name or "-")

    def get_summary(self, symbol: str) -> Optional[CompanySummary]:
        d = self._load().get(symbol)
        if not d:
            return None
        return CompanySummary(
            symbol=d["symbol"], name=d.get("name", ""),
            sector=d.get("sector", ""), conclusion=d.get("conclusion", ""),
            key_metrics=d.get("key_metrics", {}),
            insights=d.get("insights", []), updated_at=d.get("updated_at", ""))

    def peer_summaries(self, exclude: str = "",
                       limit: int = 5) -> List[CompanySummary]:
        """其他标的的沉淀摘要（跨标的横向对比注入用），按更新时间倒序"""
        items = [CompanySummary(**{
            "symbol": d["symbol"], "name": d.get("name", ""),
            "sector": d.get("sector", ""), "conclusion": d.get("conclusion", ""),
            "key_metrics": d.get("key_metrics", {}),
            "insights": d.get("insights", []), "updated_at": d.get("updated_at", ""),
        }) for d in self._load().values() if d.get("symbol") != exclude]
        items.sort(key=lambda s: s.updated_at or "", reverse=True)
        return items[:limit]

    def all_symbols(self) -> List[str]:
        return sorted(self._load())

    # ------------------------------------------------------------------
    def _load(self) -> Dict[str, dict]:
        if self._cache is None:
            if os.path.exists(self.store_path):
                try:
                    with open(self.store_path, encoding="utf-8") as f:
                        self._cache = json.load(f)
                except (json.JSONDecodeError, OSError):
                    logger.warning("长期记忆文件损坏，重建：%s", self.store_path)
                    self._cache = {}
            else:
                self._cache = {}
        return self._cache

    def _save(self, store: Dict[str, dict]):
        self._cache = store
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)


class HybridMemory:
    """三层混合记忆管理器：实时分流——临时信息入短期、结论摘要沉淀长期、
    方法论知识经外部记忆按需检索"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        default_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), *[".."] * 7,
            "reports", "finance-report", "memory"))
        mem_dir = self.config.get("memory_dir", default_dir)
        self.short_term = ShortTermMemory(
            budget=int(self.config.get("short_budget", 12000)))
        self.long_term = LongTermMemory(os.path.join(
            mem_dir, "long_term", "company_summaries.json"))
        self._retriever = self.config.get("retriever")  # RAGRetriever（外部记忆）

    # ------------------------------------------------------------------
    # 分流入口
    # ------------------------------------------------------------------
    def ingest(self, key: str, content, kind: str = "fact",
               symbol: str = "") -> str:
        """信息分流入口

        kind="table"：大表格 → 压缩预览（表头+前后5行）入短期
        kind="fact" ：数据/事实 → 原文入短期
        kind="conclusion"：分析结论 → 短期 + 长期沉淀（symbol 必填）

        返回实际进入短期记忆的内容。
        """
        if kind == "table":
            text = content if isinstance(content, str) \
                else compress_table(content)
        else:
            text = str(content)
        self.short_term.add(key, text)
        if kind == "conclusion":
            if not symbol:
                raise ValueError("kind='conclusion' 必须指定 symbol 以沉淀长期记忆")
            existing = self.long_term.get_summary(symbol)
            if existing is not None:
                # 合并语义：仅更新结论与时间，
                # 保留此前沉淀的 name/sector/key_metrics/insights
                existing.conclusion = text[:500]
                existing.updated_at = ""  # 置空由 save_summary 刷新
            else:
                existing = CompanySummary(
                    symbol=symbol, conclusion=text[:500])
            self.long_term.save_summary(existing)
        return text

    def save_analysis(self, summary: CompanySummary):
        """分析完成后的完整结论摘要沉淀（FinanceAnalyzer/Investor 调用）"""
        self.long_term.save_summary(summary)

    def retrieve_knowledge(self, query: str, top_k: int = 3) -> List[dict]:
        """外部记忆：RAG 按需检索财务方法论/行业框架"""
        if self._retriever is None:
            return []
        return [h.to_dict() for h in self._retriever.retrieve(query, top_k)]

    # ------------------------------------------------------------------
    # 上下文构建（跨标的批量分析的核心：前序结论摘要注入）
    # ------------------------------------------------------------------
    def build_context(self, symbol: str, peer_limit: int = 5,
                      peer_chars: int = 300) -> str:
        """构建当前标的分析上下文：短期记忆 + 前序标的长期摘要

        前序标的以长期记忆摘要形态注入（既控制长度又保留板块横向对比信息）。
        """
        blocks = []
        if self.short_term.keys:
            blocks.append(self.short_term.render())
        peers = self.long_term.peer_summaries(exclude=symbol, limit=peer_limit)
        if peers:
            lines = [p.as_text(max_chars=peer_chars) for p in peers]
            blocks.append("## 前序标的分析摘要（长期记忆）\n" + "\n".join(lines))
        return "\n\n".join(blocks)

    def reset_for_symbol(self, symbol: str):
        """切换标的：清空短期记忆（长期/外部记忆保留）"""
        self.short_term.clear()
        logger.info("切换标的 %s：短期记忆已清空（长期记忆 %d 家沉淀保留）",
                    symbol, len(self.long_term.all_symbols()))

    def stats(self) -> dict:
        """记忆使用统计（可复现性：资源消耗记录）"""
        return {
            "short_term_sections": len(self.short_term.keys),
            "short_term_chars": self.short_term.size,
            "short_term_budget": self.short_term.budget,
            "short_term_evicted": self.short_term.evicted,
            "long_term_symbols": self.long_term.all_symbols(),
        }
