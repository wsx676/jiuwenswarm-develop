# -*- coding: utf-8 -*-
"""新闻三阶段质量过滤 Pipeline（优化方案 1，来源：得物检索 Agent 实践）

问题：关键词命中 ≠ 语义相关（如"消费板块"搜到"消费者权益投诉"），
噪声新闻直接计入情绪统计，稀释/扭曲景气度判断，与研报"逻辑严谨性"
评审维度冲突。

三阶段设计（采集后、情绪统计前，researcher 消费阶段执行）：
- Stage 0 FastPass：结果 ≤ 2 条且来源全在权威白名单 → 直接通过（零开销）
- Stage 1 规则粗筛：实体全称命中（强相关直通），或实体核心词与主题词
  （财报/业绩/行业类）双命中，过滤纯词面命中（rerank_enabled 开关）
- Stage 2 LLM 精评：逐条相关性打分（相关 0.8 / 部分相关 0.5 / 无关 0.1），
  阈值 0.4，仅在条数 > llm_min_items 时触发，控制 Token 成本
  （llm_grade_enabled 开关，默认关）

确定性承诺：
- 总开关 enabled 默认 False，关闭时链路与旧口径一致；
- 过滤只在内存进行，不改写 data/ 采集缓存（缓存即数据资产）；
- 过滤统计累计到 RUN_STATS，随 run_stats.json 落盘
  （news_filtered: "被过滤数/召回数"，如 7/18，答辩可解释口径）。
"""

import logging
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

# 主题词表（财报/业绩/行业类）：Stage 1 要求标题或摘要至少命中其一，
# 阻断"关键词命中但无经营/行业内容"的纯词面噪声
TOPIC_WORDS = (
    # 财报与业绩
    "财报", "业绩", "营收", "净利", "净利润", "利润", "盈利", "亏损",
    "年报", "季报", "中报", "半年报", "快报", "预告", "预增", "预减",
    "增长", "下滑", "同比", "环比",
    # 公司与资本动作
    "公告", "回购", "增持", "减持", "分红", "并购", "重组", "定增",
    "融资", "上市", "退市", "股价", "市值", "估值", "股东",
    # 行业与经营
    "行业", "产业", "市场", "景气", "需求", "供给", "产能", "扩产",
    "投产", "订单", "中标", "销售", "经营", "份额", "竞争", "龙头",
    "提价", "降价", "价格",
    # 政策与监管
    "政策", "规划", "补贴", "减税", "促消费", "以旧换新", "监管",
    "处罚", "调查", "反垄断", "风险",
)

# LLM 精评打分档位（与提示词口径一致）
GRADE_RELEVANT = 0.8
GRADE_PARTIAL = 0.5
GRADE_IRRELEVANT = 0.1


class NewsQualityFilter:
    """新闻三阶段质量过滤器（config: news_filter.* 各阶段独立可开关）"""

    # 板块/行业类关键词的通用后缀（剥离后取核心实体词）
    GENERIC_SUFFIXES = ("板块", "行业", "产业", "概念", "领域")

    def __init__(self, config: Optional[dict] = None):
        cfg = (config or {}).get("news_filter") or {}
        # 总开关默认关：复现场景保持与旧口径逐字节一致
        self.enabled = bool(cfg.get("enabled", False))
        self.rerank_enabled = bool(cfg.get("rerank_enabled", True))
        self.llm_grade_enabled = bool(cfg.get("llm_grade_enabled", False))
        # LLM 精评触发门槛与阈值（条数少时规则层已足够）
        self.llm_min_items = int(cfg.get("llm_min_items", 5))
        self.llm_threshold = float(cfg.get("llm_threshold", 0.4))
        self._llm = None
        self._llm_init = False
        self._llm_config = (config or {}).get("llm")

    # ------------------------------------------------------------------
    def filter(self, news_data: dict, keyword: str = "",
               extra_entities: Optional[List[str]] = None,
               llm=None, stats=None) -> dict:
        """三阶段过滤，返回新 dict（不改原始数据）

        Args:
            news_data: NewsData.to_dict() 结构（含 items/keyword）
            keyword: 目标实体词；缺省取 news_data["keyword"]
            extra_entities: 额外实体词（如行业研报的板块成分股名，
                板块类新闻常以成分股点名而非板块全称出现）
            llm: LLM 客户端注入（测试用）；缺省惰性初始化
            stats: RunStats 注入（测试用）；缺省进程级 RUN_STATS
        """
        if not self.enabled:
            return news_data
        stats = stats or RUN_STATS
        items = news_data.get("items") or []
        total = len(items)
        if not total:
            return news_data
        keyword = keyword or news_data.get("keyword") or ""

        # Stage 0 FastPass：少量且全权威来源 → 零开销直通
        if total <= 2 and all(self._is_reliable(it.get("source", ""))
                              for it in items):
            stats.add_news_filter(keyword, total, total, fastpass=True)
            logger.info("新闻过滤 Stage 0 FastPass：%d 条全权威直通", total)
            return news_data

        kept, rule_removed, llm_removed = list(items), 0, 0

        # Stage 1 规则粗筛：实体词 + 主题词命中（保留至少 1 条兜底：
        # 全滤净说明规则匹配过严而非新闻全噪声，宁用旧口径不降级
        # "数据不足"；LLM 精评是精确语义判断，不适用此兜底）
        if self.rerank_enabled:
            before = len(kept)
            after = [it for it in kept
                     if self._rule_relevant(it, keyword, extra_entities)]
            if after:
                kept = after
                rule_removed = before - len(kept)
            else:
                logger.info("规则粗筛全滤净，兜底保留原 %d 条", before)

        # Stage 2 LLM 精评：仅条数超门槛且开关开启时触发
        if self.llm_grade_enabled and len(kept) > self.llm_min_items:
            before = len(kept)
            kept = self._llm_grade(kept, keyword, llm=llm)
            llm_removed = before - len(kept)

        stats.add_news_filter(keyword, total, len(kept),
                              rule_removed, llm_removed)
        if rule_removed or llm_removed:
            logger.info(
                "新闻过滤：召回 %d → 保留 %d（规则 -%d，LLM -%d）",
                total, len(kept), rule_removed, llm_removed)
        filtered = dict(news_data)
        filtered["items"] = kept
        filtered["count"] = len(kept)
        filtered["filter_stats"] = {
            "received": total, "kept": len(kept),
            "rule_removed": rule_removed, "llm_removed": llm_removed,
        }
        return filtered

    # ------------------------------------------------------------------
    # Stage 1 规则粗筛
    # ------------------------------------------------------------------
    def _rule_relevant(self, item: dict, keyword: str,
                       extra_entities: Optional[List[str]] = None) -> bool:
        """实体词或主题词任一命中即保留（过滤纯词面命中）

        两路信号互补：
        - 实体全称命中（标题直接点名标的）= 强相关，即便无主题词
          也保留（行情动态类新闻，如"贵州茅台跌超4%"）；
        - 主题词命中（财报/业绩/行业类）= 内容信号，允许核心词/
          简称匹配（板块类新闻很少出现板块全称）。
        extra_entities（如板块成分股名）并入实体词集参与双命中判定：
        板块类新闻常以成分股点名而非板块全称出现。
        被过滤的是"既无实体全称、又无主题词"的纯词面命中噪声。
        """
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        if keyword and keyword in text:
            return True
        entities = self._entity_terms(keyword) | set(extra_entities or ())
        return any(e in text for e in entities) and \
            any(w in text for w in TOPIC_WORDS)

    @classmethod
    def _entity_terms(cls, keyword: str) -> set:
        """目标实体匹配词集：全称 + 变体

        - 板块类（"消费板块"）：剥离通用后缀取核心词（"消费"）
        - 公司名（"贵州茅台"）：全称 + 末二字简称（"茅台"，
          与新浪滚动源匹配同口径）
        """
        terms = {keyword} if keyword else set()
        core, generic = keyword, False
        for suf in cls.GENERIC_SUFFIXES:
            if core.endswith(suf) and len(core) > len(suf):
                core, generic = core[:-len(suf)], True
                break
        if generic:
            terms.add(core)
        elif len(keyword) >= 4:
            terms.add(keyword[-2:])
        return {t for t in terms if len(t) >= 2}

    @staticmethod
    def _is_reliable(source: str) -> bool:
        from collectors.news_collector import NewsCollector
        return any(s in source for s in NewsCollector.RELIABLE_SOURCES)

    # ------------------------------------------------------------------
    # Stage 2 LLM 精评
    # ------------------------------------------------------------------
    def _llm_grade(self, items: List[dict], keyword: str, llm=None
                   ) -> List[dict]:
        """逐条相关性打分保留 ≥ 阈值者；LLM 失败降级跳过本阶段"""
        llm = llm or self._get_llm()
        if llm is None:
            logger.info("无可用 LLM Key，Stage 2 精评跳过")
            return items
        lines = "\n".join(
            f"{i + 1}. {it.get('title', '')}"
            f"（{it.get('source', '')}）"
            + (f"：{it.get('summary', '')[:50]}" if it.get("summary") else "")
            for i, it in enumerate(items))
        prompt = (
            f"你是金融新闻相关性评审员。研究主题：「{keyword}」。\n"
            f"请为下列 {len(items)} 条新闻逐条评估与主题的相关性：\n"
            f"{lines}\n\n"
            f"打分标准：{GRADE_RELEVANT} = 相关（直接讨论主题实体的"
            f"经营/业绩/行业/政策）；{GRADE_PARTIAL} = 部分相关"
            f"（提及但非主要内容）；{GRADE_IRRELEVANT} = 无关"
            f"（纯关键词字面命中）。\n"
            "严格输出 JSON 数字数组，与条目顺序一一对应，"
            f"例如 [0.8, 0.1, 0.5]。")
        try:
            result = llm.chat_json(
                prompt, max_tokens=min(300, len(items) * 12 + 40),
                temperature=0.1)
        except Exception as e:  # noqa: BLE001 精评失败降级保留规则层结果
            logger.warning("LLM 新闻精评失败，跳过 Stage 2: %s", e)
            return items
        scores = self._parse_scores(result, len(items))
        if scores is None:
            logger.warning("LLM 新闻精评输出无法解析，跳过 Stage 2")
            return items
        kept = [it for it, s in zip(items, scores)
                if s is None or s >= self.llm_threshold]
        return kept

    @staticmethod
    def _parse_scores(result, n: int) -> Optional[List[Optional[float]]]:
        """解析 LLM 输出：数字数组（顺序对应）或 {index, score} 对象数组；
        无法解析返回 None；个别缺位记 None（保守保留）"""
        if not isinstance(result, list):
            return None
        scores: List[Optional[float]] = [None] * n
        ok = False
        for i, v in enumerate(result[:n]):
            if isinstance(v, (int, float)):
                scores[i] = float(v)
                ok = True
            elif isinstance(v, dict):
                idx = v.get("index", i + 1)
                try:
                    pos = int(idx) - 1
                    scores[pos] = float(v.get("score"))
                    ok = True
                except (TypeError, ValueError, IndexError):
                    continue
        return scores if ok else None

    def _get_llm(self):
        """惰性初始化 LLM 客户端（无 Key 时返回 None，跳过 Stage 2）"""
        if not self._llm_init:
            self._llm_init = True
            from common.llm_client import LLMClient
            llm = LLMClient(self._llm_config)
            if llm.api_key:
                self._llm = llm
        return self._llm
