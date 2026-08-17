# -*- coding: utf-8 -*-
"""宏观分析器

聚焦宏观经济指标与政策趋势分析：
- 核心指标：GDP / CPI / PMI（akshare 宏观接口，标注数据期与来源，
  接口不可用时降级为空并说明，不编造数据）
- 政策趋势：财政政策与货币政策方向（政策类新闻关键词规则化提炼）
- 板块映射：宏观环境对六大板块的差异化影响（规则传导框架）

输出结构与财务分析器类似：结构化指标 + 分析洞察。
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# akshare 宏观接口与列名关键词（值列按关键词匹配，取最新一期）
# 列名为接口真实列名：PMI 为「制造业-指数」（非「当月值」）
INDICATOR_SOURCES = {
    "GDP": {"func": "macro_china_gdp", "column": "同比增长"},
    "CPI": {"func": "macro_china_cpi", "column": "同比"},
    "PMI": {"func": "macro_china_pmi", "column": "制造业-指数"},
}
INDICATOR_SOURCE_DESC = "国家统计局（经 akshare macro_china_* 接口）"

# 值域健全性校验：越界视为取错列/取错期，降级跳过（防错值进报告）
VALUE_RANGES = {
    "GDP": (-5.0, 30.0),   # 中国 GDP 同比历史区间约 -5%~15%
    "CPI": (-5.0, 10.0),
    "PMI": (30.0, 70.0),   # PMI 定义上不可能为负，实际集中于 40~60
}

# 货币/财政政策信号词表（规则化提炼，可解释）
MONETARY_EASING = ("降准", "降息", "逆回购", "LPR下调", "宽松", "流动性投放")
MONETARY_TIGHT = ("加息", "收紧", "提准", "缩表")
FISCAL_ACTIVE = ("专项债", "减税降费", "以旧换新", "促消费", "补贴",
                 "财政支出", "设备更新")
FISCAL_TIGHT = ("财政紧缩", "压缩支出", "增税")

# 板块影响映射的关键词（公司池板块名匹配用）
SECTOR_KEYWORDS = {
    "消费": "消费",
    "金融": "金融",
    "新能源/电力": "新能源",
    "科技/AI/半导体": "科技",
    "周期/资源": "周期",
    "高端制造/基建": "高端制造",
}


@dataclass
class MacroAnalysis:
    """宏观分析结果"""
    indicators: dict = field(default_factory=dict)   # GDP/CPI/PMI 等
    policy_trends: dict = field(default_factory=dict)  # 政策趋势
    sector_impact: dict = field(default_factory=dict)  # 对各板块的影响判断
    insights: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "indicators": self.indicators,
            "policy_trends": self.policy_trends,
            "sector_impact": self.sector_impact,
            "insights": self.insights,
        }


class MacroAnalyzer:
    """宏观分析器"""

    def analyze(self, news_data: Optional[dict] = None) -> MacroAnalysis:
        """分析当前宏观环境与政策趋势

        Args:
            news_data: 宏观/政策相关新闻采集结果（含 items 列表）
        """
        result = MacroAnalysis()

        result.indicators = self._collect_indicators()
        result.policy_trends = self._analyze_policy(news_data or {})
        result.sector_impact = self._map_sector_impact(result)
        result.insights = self._generate_insights(result)
        return result

    # ------------------------------------------------------------------
    def _collect_indicators(self) -> dict:
        """获取最新宏观指标（GDP/CPI/PMI，公开渠道；失败降级跳过）"""
        indicators = {}
        try:
            import akshare as ak
        except ImportError:
            logger.warning("akshare 未安装，宏观指标跳过")
            return indicators
        for name, spec in INDICATOR_SOURCES.items():
            try:
                indicators[name] = self._fetch_series(ak, spec, name)
            except Exception as e:  # noqa: BLE001 单指标失败不拖垮整体
                logger.warning("宏观指标 %s 获取失败: %s", name, e)
        return indicators

    @staticmethod
    def _fetch_series(ak, spec: dict, name: str) -> dict:
        """通用序列提取：时间列显式升序排序取最新期，列名按关键词匹配

        akshare macro_china_* 接口行序不保证（部分为新→旧），
        直接 iloc[-1] 会取到最旧一期，必须按时间列排序后再取末行。
        """
        df = getattr(ak, spec["func"])()
        df = df.sort_values(by=df.columns[0])
        last = df.iloc[-1]
        # 值列：优先列名含关键词的数值列，否则最后一个数值列
        value_col = None
        for col in df.columns:
            if spec["column"] in str(col):
                value_col = col
                break
        if value_col is None:
            for col in reversed(df.columns):
                try:
                    float(last[col])
                    value_col = col
                    break
                except (TypeError, ValueError):
                    continue
        if value_col is None:
            raise ValueError(f"{name} 未找到数值列")
        value = float(last[value_col])
        lo, hi = VALUE_RANGES.get(name, (float("-inf"), float("inf")))
        if not lo <= value <= hi:
            raise ValueError(
                f"{name} 值 {value} 越界 [{lo}, {hi}]"
                f"（列：{value_col}，疑似取错列）")
        return {
            "period": str(last[df.columns[0]]),
            "value": round(value, 2),
            "column": str(value_col),
            "source": INDICATOR_SOURCE_DESC,
        }

    # ------------------------------------------------------------------
    def _analyze_policy(self, news_data: dict) -> dict:
        """分析财政/货币政策趋势：政策类新闻关键词规则化提炼"""
        items = news_data.get("items", [])
        text = " ".join(
            f"{it.get('title', '')} {it.get('summary', '')}" for it in items)

        def hits(words) -> List[str]:
            return sorted({w for w in words if w in text})

        monetary_easing = hits(MONETARY_EASING)
        monetary_tight = hits(MONETARY_TIGHT)
        if monetary_easing and not monetary_tight:
            monetary = "宽松"
        elif monetary_tight and not monetary_easing:
            monetary = "收紧"
        else:
            monetary = "中性"

        fiscal_active = hits(FISCAL_ACTIVE)
        fiscal_tight = hits(FISCAL_TIGHT)
        if fiscal_active and not fiscal_tight:
            fiscal = "积极"
        elif fiscal_tight and not fiscal_active:
            fiscal = "收缩"
        else:
            fiscal = "中性"

        return {
            "monetary": {"direction": monetary,
                         "signals": monetary_easing + monetary_tight},
            "fiscal": {"direction": fiscal,
                       "signals": fiscal_active + fiscal_tight},
            "news_count": len(items),
        }

    # ------------------------------------------------------------------
    def _map_sector_impact(self, result: MacroAnalysis) -> dict:
        """宏观环境对六大板块的差异化影响映射（规则传导框架）"""
        impact = {}
        pmi = result.indicators.get("PMI", {}).get("value")
        cpi = result.indicators.get("CPI", {}).get("value")
        monetary = result.policy_trends.get(
            "monetary", {}).get("direction", "中性")
        fiscal = result.policy_trends.get("fiscal", {}).get("direction", "中性")

        for sector, kw in SECTOR_KEYWORDS.items():
            notes = []
            if kw == "消费":
                if cpi is not None:
                    if 0 <= cpi <= 3:
                        notes.append(f"CPI {cpi}% 温和通胀，消费需求平稳")
                    else:
                        notes.append(f"CPI {cpi}% 偏离温和区间，"
                                     "关注必选消费提价传导")
                if fiscal == "积极":
                    notes.append("积极财政含促消费/以旧换新，直接受益")
            elif kw == "金融":
                if monetary == "宽松":
                    notes.append("宽松环境下银行息差承压，"
                                 "券商受益于流动性改善")
                else:
                    notes.append("货币政策中性，金融板块随基本面波动")
            elif kw == "新能源":
                if fiscal == "积极":
                    notes.append("财政积极（专项债/设备更新）利好电力"
                                 "与新能源基建投资")
                if pmi is not None and pmi >= 50:
                    notes.append(f"PMI {pmi} 处扩张区间，装机与开工需求有支撑")
            elif kw == "科技":
                if monetary == "宽松":
                    notes.append("流动性宽松利好成长估值（科技/AI）")
                notes.append("国产替代与 AI 产业趋势为主要驱动")
            elif kw == "周期":
                if pmi is not None:
                    notes.append(
                        f"PMI {pmi} "
                        + ("制造业扩张，工业资源品需求有支撑"
                           if pmi >= 50 else "制造业收缩，资源品需求承压"))
            elif kw == "高端制造":
                if pmi is not None:
                    notes.append(
                        f"PMI {pmi} "
                        + ("制造业景气扩张，装备制造订单向好"
                           if pmi >= 50 else "制造业景气偏弱，关注政策对冲"))
                if fiscal == "积极":
                    notes.append("积极财政（设备更新/基建）形成拉动")
            impact[sector] = "；".join(notes) or "宏观信号中性，按行业自身周期判断"
        return impact

    # ------------------------------------------------------------------
    def _generate_insights(self, result: MacroAnalysis) -> List[str]:
        insights = []
        for name in ("GDP", "CPI", "PMI"):
            ind = result.indicators.get(name)
            if ind:
                insights.append(
                    f"{name} 最新值 {ind['value']}（{ind['period']}，"
                    f"口径：{ind['column']}，来源：{ind['source']}）"
                )
        if not result.indicators:
            insights.append("宏观指标接口暂不可用，宏观分析以政策趋势为主")
        monetary = result.policy_trends.get("monetary", {})
        fiscal = result.policy_trends.get("fiscal", {})
        insights.append(
            f"货币政策方向：{monetary.get('direction', '中性')}"
            + (f"（信号：{'、'.join(monetary['signals'][:4])}）"
               if monetary.get("signals") else "")
        )
        insights.append(
            f"财政政策方向：{fiscal.get('direction', '中性')}"
            + (f"（信号：{'、'.join(fiscal['signals'][:4])}）"
               if fiscal.get("signals") else "")
        )
        return insights
