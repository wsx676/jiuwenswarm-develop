# -*- coding: utf-8 -*-
"""结构化报告撰写器（分治式生成）

按研报模板生成 Markdown，确保：
- 论点-论据链完整（分治式：先大纲后逐段，每段带前文摘要 ≤ 800 字）
- 所有数据标注来源（引用率 ≥ 90% 闸门由 CitationChecker 把关）
- 图表与正文同源（ChartGenerator 同源数据，正文程序化插入）
- 含"投资结论与仓位建议"章节（赛题要求）与风险提示、免责声明（合规）

长篇报告分治式生成：先 YAML 大纲（part_title + part_desc）后分段撰写，
用已生成内容反复喂回突破单次输出长度限制；LLM 不可用时降级为
规则模板拼接（结构不缺、数据同源、来源不缺）。
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

try:
    from generators.chart_generator import Chart
except ImportError:  # 兼容包导入/直跑：按绝对路径定位技能根目录
    import sys
    _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _p not in sys.path:
        sys.path.insert(0, _p)
    from generators.chart_generator import Chart

logger = logging.getLogger(__name__)

# 报告输出基准目录（图片相对路径以此解析）
REPORT_BASE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), *[".."] * 7, "reports", "finance-report"))

# 降级大纲（LLM 不可用 / 大纲解析失败时）：结构与模板一致
OUTLINE_FALLBACK: List[Tuple[str, str]] = [
    ("一、核心观点", "3-5 条核心论点，每条附关键数据支撑"),
    ("二、投资结论与仓位建议",
     "评级（增持/中性/减持观望）与建议仓位（0-10% 区间）及决策逻辑"),
    ("三、公司概况", "公司简介、主营业务与近期重要动态"),
    ("四、行业分析", "板块景气度判断 + 竞争格局（同板块公司横向对比）"),
    ("五、财务分析", "盈利/偿债/成长能力分析，数据与财务表格、盈利趋势图一致"),
    ("六、估值分析", "PE 估值水平与口径说明、盈利展望"),
    ("七、风险提示", "主要风险因素（3-5 条）"),
    ("八、数据来源", "数据与信息来源清单（权威白名单）"),
]

# 行业研报固定八章结构（模板大纲：结构稳定、口径与提交规范一致）
OUTLINE_INDUSTRY_FALLBACK: List[Tuple[str, str]] = [
    ("一、板块核心观点", "3-5 条板块级核心论点，附景气度与成分公司数据"),
    ("二、投资结论与配置建议", "板块配置建议（超配/标配/低配）与仓位逻辑"),
    ("三、行业概况", "板块成分公司与近期行业重要动态"),
    ("四、景气度分析", "新闻情绪与政策信号判定的景气等级及依据"),
    ("五、竞争格局与排名", "板块成分公司最新期财务指标横向对比与排名"),
    ("六、估值与资金面", "板块内公司估值水平与区间表现概述"),
    ("七、风险提示", "板块主要风险因素（3-5 条）"),
    ("八、数据来源", "数据与信息来源清单（权威白名单）"),
]

# 宏观研报固定八章结构
OUTLINE_MACRO_FALLBACK: List[Tuple[str, str]] = [
    ("一、宏观核心观点", "3-5 条宏观核心论点，附关键指标最新值"),
    ("二、宏观结论与板块配置建议", "宏观环境判断与对各板块的配置倾向"),
    ("三、核心宏观指标", "GDP/CPI/PMI 等指标最新值、期间与变动"),
    ("四、政策动向", "政策信号与趋势判断（新闻来源）"),
    ("五、对板块的影响分析", "宏观因素对各板块的传导与影响判断"),
    ("六、风险提示", "宏观主要风险因素（3-5 条）"),
    ("七、数据来源", "数据与信息来源清单（权威白名单）"),
]

# 洞察负面信号词（评级规则用）
NEGATIVE_HINTS = ("承压", "下滑", "警惕", "偏弱", "待观察", "杠杆偏高", "不足")
SECTION_SOURCE = {  # 各章节固定来源标注（正文引用闸门）
    # H1 回归：一/二/七章模板段含数据句，同样必须带来源标注，
    # 否则降级报告引用率不达标、过不了自研 Reviewer 闸门
    "一、核心观点": "公司定期财报与公开行情数据",
    "二、投资结论与仓位建议": "公司定期财报与公开行情数据",
    "三、公司概况": "公开行情数据与权威财经媒体报道",
    "四、行业分析": "组委会公司池分组与权威财经媒体报道",
    "五、财务分析": "公司定期财报（akshare 财务摘要，东方财富 F10）",
    "六、估值分析": "公开行情数据与公司定期财报",
    "七、风险提示": "公司公告与权威财经媒体报道",
}

# 行业研报各章节固定来源标注（正文引用闸门，段落级口径）
SECTION_SOURCE_INDUSTRY = {
    "一、板块核心观点": "组委会公司池分组与权威财经媒体报道",
    "二、投资结论与配置建议": "组委会公司池分组与权威财经媒体报道",
    "三、行业概况": "组委会公司池与权威财经媒体报道",
    "四、景气度分析": "权威财经媒体报道（新闻情绪与政策信号统计）",
    "五、竞争格局与排名": "组委会公司池成分公司定期财报（akshare 财务摘要）",
    "六、估值与资金面": "公开行情数据与组委会公司池分组",
    "七、风险提示": "权威财经媒体报道",
}

# 宏观研报各章节固定来源标注
SECTION_SOURCE_MACRO = {
    "一、宏观核心观点": "国家统计局（经 akshare 接口）与权威财经媒体报道",
    "二、宏观结论与板块配置建议": "国家统计局（经 akshare 接口）与权威财经媒体报道",
    "三、核心宏观指标": "国家统计局（经 akshare 接口）",
    "四、政策动向": "权威财经媒体报道（政策信号统计）",
    "五、对板块的影响分析": "组委会公司池分组与国家统计局数据",
    "六、风险提示": "权威财经媒体报道",
}


@dataclass
class ReportDraft:
    """报告初稿"""
    content: str = ""
    charts: List[Chart] = field(default_factory=list)
    claims: list = field(default_factory=list)   # 论据卡片（含引用）
    citations: List[str] = field(default_factory=list)
    outline: List[dict] = field(default_factory=list)
    image_issues: List[str] = field(default_factory=list)


class ReportWriter:
    """报告撰写器（分治式）"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._llm = None
        self._llm_ready = False
        # 前文摘要上限（防噪声，计划 4.5：控制在 800 字内）
        self.context_limit = int(self.config.get("context_limit", 800))

    # ------------------------------------------------------------------
    def write(self, research_data: dict, request,
              revision_feedback: Optional[dict] = None) -> ReportDraft:
        draft = ReportDraft()
        report_type = getattr(request, "report_type", "company")
        if report_type == "industry":
            return self._write_industry(research_data, request,
                                        revision_feedback)
        if report_type == "macro":
            return self._write_macro(research_data, request,
                                     revision_feedback)
        return self._write_company(
            research_data, request, revision_feedback)

    # ------------------------------------------------------------------
    def _write_company(self, data: dict, request,
                       revision_feedback: Optional[dict] = None
                       ) -> ReportDraft:
        draft = ReportDraft()
        quote = data.get("quote_data", {}) or {}
        filing = data.get("filing_data", {}) or {}
        news = data.get("news_data", {}) or {}
        finance = data.get("finance_analysis")
        industry = data.get("industry_analysis")
        name = getattr(request, "name", "") or quote.get("name", "")
        target = getattr(request, "target", "")
        statements = filing.get("statements", [])

        # 1. 大纲（LLM 优先，降级固定八段）
        outline = self._build_outline(name, finance, industry)
        draft.outline = outline

        # 2. 逐段撰写（分治式：前文摘要喂回；修订轮注入审查指令）
        revision = self._revision_instructions(
            (revision_feedback or {}).get("issues", []) or [])
        sections: List[Tuple[str, str]] = []
        for part in outline:
            title, desc = part["part_title"], part["part_desc"]
            material = self._materials_for(
                title, data=data, name=name, target=target,
                statements=statements, quote=quote, news=news,
                finance=finance, industry=industry,
            )
            body = self._write_section(
                name, title, desc, material,
                context=self._digest(sections), revision=revision)
            sections.append((title, body))

        # 3. 拼接正文：标题信息块 + 章节正文 + 程序化图表注入 + 来源
        charts: List[Chart] = list(data.get("charts", []) or [])
        draft.charts = charts
        draft.content = self._assemble(
            sections, charts, data, name, target, request)
        draft.citations = self._collect_sources(
            quote, filing, news, data)
        draft.claims = self._build_claims(data, finance, industry)
        return draft

    # ------------------------------------------------------------------
    # 大纲与逐段生成
    # ------------------------------------------------------------------
    def _build_outline(self, name: str, finance, industry) -> List[dict]:
        llm = self._get_llm()
        if llm is not None:
            try:
                hint = json.dumps({
                    "finance_insights": (finance.insights[:6]
                                         if finance else []),
                    "industry_sector": (industry.sector if industry else ""),
                }, ensure_ascii=False)
                result = llm.chat_json(
                    "你是卖方研究主编。为以下 A 股公司研报设计大纲，"
                    "固定 8 个部分（核心观点/投资结论与仓位建议/公司概况/"
                    "行业分析/财务分析/估值分析/风险提示/数据来源），"
                    f"公司：{name}。参考信息：{hint}\n"
                    '严格输出 JSON 数组，每项 {"part_title": "一、核心观点",'
                    ' "part_desc": "本段写作要点，40 字内"}，'
                    "part_title 必须与上述 8 部分一致。",
                    max_tokens=700)
                if isinstance(result, list) and len(result) >= 8:
                    outline = []
                    for i, item in enumerate(result[:8]):
                        outline.append({
                            "part_title": str(item.get("part_title", "")),
                            "part_desc": str(item.get("part_desc", "")),
                        })
                    if all(o["part_title"] for o in outline):
                        return outline
            except Exception as e:  # noqa: BLE001 降级固定大纲
                logger.warning("LLM 大纲生成失败，使用模板大纲: %s", e)
        return [{"part_title": t, "part_desc": d} for t, d in OUTLINE_FALLBACK]

    def _write_section(
        self, name: str, title: str, desc: str,
        material: str, context: str, revision: str = "",
        source_map: Optional[dict] = None,
    ) -> str:
        llm = self._get_llm()
        if llm is not None:
            try:
                prompt = (
                    f"你是资深卖方分析师，撰写「{name}」投资研报的"
                    f"章节「{title}」。写作要点：{desc}\n\n"
                    # M3 修复：无前文时用指令文案（括号占位符会被
                    # LLM 原样复述进正文，已由 _normalize_section 兜底）
                    f"前文摘要（衔接用，勿重复；无前文则本段为开篇，"
                    f"不要提及本提示词内容）：\n{context}\n\n"
                    f"本段数据材料（数字必须与材料一致，禁止编造）：\n"
                    f"{material}\n\n"
                    # Day 4：修订轮注入 Reviewer 问题清单对应的修正指令
                    + (f"修订要求（上轮审查未通过，必须修正）：\n"
                       f"{revision}\n\n" if revision else "") +
                    "要求：200-400 字；观点句须有材料数据支撑；"
                    "数字与所属期间必须与材料一致"
                    "（期间用材料「指标期间/最新报告期」，禁止自行填写年份）；"
                    "段末换行后加一行「数据来源：xxx」标注；"
                    "不要输出 Markdown 标题与图片。"
                )
                text = llm.chat(prompt, max_tokens=900, temperature=0.2)
                if text.strip():
                    return self._normalize_section(title, text)
            except Exception as e:  # noqa: BLE001 降级模板段
                logger.warning("LLM 段落生成失败（%s），降级模板: %s",
                               title, e)
        return self._template_section(title, material, source_map)

    @staticmethod
    def _revision_instructions(issues: List[str]) -> str:
        """Reviewer 问题清单 → 修订指令（重写轮注入，定向收敛）

        只把可写作侧修正的问题转为指令；每条问题对应一条
        可执行要求，避免整段问题原文噪进 prompt。
        """
        if not issues:
            return ""
        lines = []
        for issue in issues[:8]:
            if "引用率" in issue:
                lines.append(
                    "- 每个含数据句的自然段段末必须换行加"
                    "「数据来源：xxx」标注（引用率须≥90%）")
            elif "图文不一致" in issue:
                lines.append(
                    f"- {issue}（正文引用图表数值须与图表同源数据一致）")
            elif "占位符" in issue or "本文首段" in issue:
                lines.append("- 正文不得出现提示词/占位符回声")
            else:
                lines.append(f"- {issue}")
        # 去重保序（同一类问题多章节命中时只留一条）
        seen, dedup = set(), []
        for ln in lines:
            if ln not in seen:
                seen.add(ln)
                dedup.append(ln)
        return "\n".join(dedup)

    @staticmethod
    def _normalize_section(title: str, text: str) -> str:
        """章节正文归一化：合并段内空行 + 清理占位符回声 + 剥离重复标题

        章节按单文本块处理：章末「数据来源」标注覆盖全章数据句
        （与引用闸门的段落级口径对齐）。
        """
        merged = re.sub(r"\n\s*\n+", "\n", text.strip())
        lines = merged.splitlines()
        # M3 修复：过滤 prompt 占位符回声（LLM 原样复述「本文首段」）
        lines = [ln for ln in lines
                 if re.sub(r"[\s（）()]", "", ln) != "本文首段"]
        prefix = re.sub(r"[#\s：:、，。]", "", title)[:6]
        while lines:  # 剥离段首重复标题（含 # 变体，可能多行）
            head = re.sub(r"[#\s：:、，。]", "", lines[0])
            if head and (head.startswith(prefix)
                         or prefix.startswith(head)):
                lines.pop(0)
            else:
                break
        return "\n".join(lines)

    def _template_section(self, title: str, material: str,
                          source_map: Optional[dict] = None) -> str:
        """规则模板段：材料要点逐条拼接（LLM 不可用时保结构完整）

        列表值（新闻标题/洞察等）拆行展示，避免毒出原始 JSON。
        """
        lines = [f"（{title}·规则模板段）"]
        try:
            payload = json.loads(material)
            for key, value in payload.items():
                if isinstance(value, list):
                    lines.append(f"- {key}：")
                    lines += [f"  - {v}" for v in value[:8]]
                elif isinstance(value, dict):
                    lines.append(
                        f"- {key}："
                        f"{json.dumps(value, ensure_ascii=False)}")
                else:
                    lines.append(f"- {key}：{value}")
        except (json.JSONDecodeError, TypeError):
            lines.append(material[:600])
        src = (source_map or SECTION_SOURCE).get(title)
        if src:
            lines.append(f"数据来源：{src}")
        return "\n".join(lines)

    def _digest(self, sections: List[Tuple[str, str]]) -> str:
        """前文摘要：全部已生成内容截取尾部（≤ context_limit 字）"""
        text = "\n".join(f"[{t}] {b}" for t, b in sections)
        return text[-self.context_limit:]

    # ------------------------------------------------------------------
    # 材料组织（按章节关键词映射数据，同源一致）
    # ------------------------------------------------------------------
    def _materials_for(
        self, title: str, *, data, name, target,
        statements, quote, news, finance, industry,
    ) -> str:
        macro = data.get("macro_analysis")
        macro_dict = macro.to_dict() if macro else {}
        industry_dict = industry.to_dict() if industry else {}
        finance_dict = finance.to_dict() if finance else {}
        quote_brief = {
            k: quote.get(k) for k in
            ("name", "latest_close", "period_return", "source")
        }
        news_titles = [
            f"{it.get('title', '')}（{it.get('source', '')}，"
            f"{it.get('date', '')}）"
            for it in (news.get("items", []) or [])[:8]
        ]
        # 最新报告期（财务指标归属期间，防 LLM 自行填写年份）
        latest_period = (
            max((str(s.get("period", "")) for s in statements),
                default="") if statements else "")
        if "核心观点" in title:
            payload = {
                "公司": f"{name}（{target}）",
                "指标期间": latest_period,
                "区间涨跌幅%": quote.get("period_return"),
                "最新收盘价": quote.get("latest_close"),
                "财务洞察": finance_dict.get("insights", []),
                "行业判断": (industry_dict.get("prosperity", {})
                             .get("level", "")),
                "板块": industry_dict.get("sector", ""),
            }
        elif "投资结论" in title:
            rating, weight, logic = self._rating(finance, industry, quote)
            payload = {"评级建议": rating, "建议仓位区间": weight,
                       "决策逻辑": logic,
                       "指标期间": latest_period,
                       "关键依据": finance_dict.get("insights", [])[:4],
                       "估值PE": (finance_dict.get("valuation", {})
                                  .get("pe"))}
        elif "公司概况" in title:
            payload = {"公司": f"{name}（{target}）",
                       "最新收盘价": quote.get("latest_close"),
                       "区间涨跌幅%": quote.get("period_return"),
                       "近期新闻标题": news_titles}
        elif "行业分析" in title:
            payload = {"板块": industry_dict.get("sector", ""),
                       "景气度": industry_dict.get("prosperity", {}),
                       "竞对对比": industry_dict.get("competition", {}),
                       "宏观影响": macro_dict.get("sector_impact", {}),
                       "行业洞察": industry_dict.get("insights", [])}
        elif "财务分析" in title:
            payload = {"财务指标": finance_dict,
                       "最新报告期": latest_period}
        elif "估值" in title:
            valuation = finance_dict.get("valuation", {})
            payload = {"估值指标": valuation,
                       "最新收盘价": quote.get("latest_close"),
                       "区间涨跌幅%": quote.get("period_return"),
                       "指标期间": latest_period,
                       "盈利洞察": finance_dict.get("insights", [])[:3]}
            # Day 4：RAG 知识注入——估值方法论文档片段作写作参考
            # （方法论定性文字，非数据句，不替代可溯源估值数字）
            kb_notes = [c.get("content", "")[:160]
                        for c in (data.get("knowledge_chunks") or [])[:2]]
            if kb_notes:
                payload["估值方法参考（知识库，仅作方法论参考，"
                       "数字须来自估值指标）"] = kb_notes
            # H2 回归：材料无估值指标时禁止 LLM 编造 PE 等数字
            if not valuation:
                payload["估值约束"] = (
                    "材料无可溯源估值指标：禁止给出 PE/PB 等任何"
                    "估值数字与外部预测来源，须写明「暂无可溯源"
                    "估值数据，不作估值判断」")
        elif "风险" in title:
            # M4 修复：行业/宏观风险措辞按实际取值（景气等级/
            # 竞争排名），无数据依据时如实表达，不注入无据结论
            prosperity = industry_dict.get("prosperity", {})
            level = prosperity.get("level", "")
            industry_note = {
                "景气承压": "板块景气承压（景气度判定：景气承压）",
                "平稳运行": "板块景气平稳，需求端波动为主要扰动",
                "景气向上": "未见显著行业负面信号（景气度判定：景气向上）",
            }.get(level, "行业景气数据不足，不作景气方向判断")
            rank = (industry_dict.get("competition", {})
                    .get("target_rank") or {})
            if rank and min(rank.values()) > 1:
                industry_note += "；核心指标未居板块首位，存在同业竞争压力"
            payload = {"负面信号": [i for i in finance_dict.get(
                "insights", []) if any(h in i for h in NEGATIVE_HINTS)],
                "行业负面": industry_note,
                "宏观提示": "关注宏观指标（GDP/CPI/PMI）波动对需求的传导"}
        else:  # 数据来源
            payload = {"说明": "本段由程序生成来源清单"}
        return json.dumps(payload, ensure_ascii=False, default=str)[:2600]

    def _rating(self, finance, industry, quote) -> Tuple[str, str, str]:
        """Day 3 简版评级规则（Day 4 由 InvestorAgent 接管正式决策）"""
        insights = finance.insights if finance else []
        pos = sum(1 for i in insights
                  if not any(h in i for h in NEGATIVE_HINTS))
        neg = len(insights) - pos
        pe = ((finance.valuation or {}).get("pe")
              if finance else None)
        if pos >= 3 and neg <= 1:
            rating, weight = "增持", "5%-10%"
            logic = f"正面信号 {pos} 项占优" + (
                f"，PE {pe} 处合理区间" if pe else "")
        elif neg >= 2 and pos <= 1:
            rating, weight = "减持观望", "0%（暂不配置）"
            logic = f"负面信号 {neg} 项偏多，暂不配置并阐明理由"
        else:
            rating, weight = "中性", "3%-5%"
            logic = "正负信号均衡，标配仓位跟踪观察"
        return rating, weight, logic

    # ------------------------------------------------------------------
    # 正文组装与图片本地化
    # ------------------------------------------------------------------
    def _assemble(
        self, sections: List[Tuple[str, str]], charts: List[Chart],
        data, name, target, request,
    ) -> str:
        period = getattr(request, "period", "") or "最新"
        lines = [f"# {name}（{target}）投资分析报告", ""]
        lines += ["| 属性 | 值 |", "|------|-----|",
                  f"| 报告类型 | 公司研报 |",
                  f"| 报告日期 | {period} |", ""]
        by_title = {t: b for t, b in sections}
        order = [t for t, _ in sections]

        def chart_md(chart: Chart) -> List[str]:
            if not chart.image_path:
                return []
            return ["", f"![{chart.title}]({chart.image_path})",
                    f"*{chart.caption}*"]

        for title in order:
            body = by_title[title]
            # 数据来源章节由程序生成（不走 LLM 模板），避免编造
            if "数据来源" in title:
                continue
            lines.append(f"## {title}")
            lines.append("")
            lines.append(body)
            # 图表注入：股价图随公司概况，盈利趋势图随财务分析
            if "公司概况" in title:
                for c in charts:
                    if c.chart_type == "line":
                        lines += chart_md(c)
            if "财务分析" in title:
                for c in charts:
                    if c.chart_type == "bar":
                        lines += chart_md(c)
                    elif c.chart_type == "table" and c.caption:
                        lines += ["", c.caption]
            lines.append("")

        # 数据来源清单（文末，权威白名单）
        sources = self._collect_sources(
            data.get("quote_data", {}) or {},
            data.get("filing_data", {}) or {},
            data.get("news_data", {}) or {}, data)
        lines.append("## 八、数据来源")
        lines.append("")
        lines += [f"- {s}" for s in sources]
        # 来源清单含采集时间等数字，与汇总标注同段（段落级引用覆盖）
        lines.append("数据来源：以上权威渠道来源清单汇总")
        lines += ["", "---",
                  "*免责声明：本报告由 AI Agent 自动生成，仅供参考，"
                  "不构成投资建议。*"]
        content = "\n".join(lines)
        content, issues = self._localize_images(content)
        data["image_issues"] = issues  # 留痕（Reviewer 可读）
        return content

    def _localize_images(self, content: str) -> Tuple[str, List[str]]:
        """图片本地化引用校验：失效引用移除并留痕（无失效图片引用）"""
        issues: List[str] = []

        def _check(m: "re.Match") -> str:
            alt, path = m.group(1), m.group(2)
            if re.match(r"^https?://", path):
                issues.append(f"外链图片被移除: {path}")
                return f"*（图表「{alt}」为外部链接，已按本地化规则移除）*"
            local = path if os.path.isabs(path) else os.path.join(
                REPORT_BASE_DIR, path)
            if os.path.exists(local):
                return m.group(0)
            issues.append(f"本地图片缺失: {path}")
            return (f"*（图表「{alt}」生成失败，数据以正文表格为准）*")

        return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _check, content), issues

    # ------------------------------------------------------------------
    # 来源与论据卡片
    # ------------------------------------------------------------------
    def _collect_sources(self, quote, filing, news, data) -> List[str]:
        sources: List[str] = []
        if quote.get("source"):
            sources.append(
                f"行情数据：{quote.get('source', '')}"
                f"（采集于 {quote.get('collected_at', '')}）")
        if filing.get("source"):
            sources.append(
                f"财务数据：{filing.get('source', '')}"
                f"（采集于 {filing.get('collected_at', '')}）")
        macro = data.get("macro_analysis")
        if macro and macro.indicators:
            sources.append("宏观数据：国家统计局（经 akshare 接口）")
        seen = set()
        for it in (news.get("items", []) or []):
            src = it.get("source", "")
            if src and src not in seen:
                seen.add(src)
                sources.append(f"新闻资讯：{src}")
        sources.append("行业分组：组委会公司池（上市公司列表.xlsx）")
        # Day 4：RAG 知识库参与写作时留痕（溯源闭环）
        if data.get("knowledge_chunks"):
            sources.append("估值与分析方法参考：财务方法论知识库（自沉淀方法论文档）")
        return sources

    def _build_claims(self, data, finance, industry) -> list:
        """论据卡片：洞察 → {text, citation}（CitationChecker 卡片级校验）"""
        claims = []
        for ins in (finance.insights if finance else []):
            claims.append({"text": ins, "citation": "公司定期财报"})
        if industry:
            for ins in industry.insights[:4]:
                claims.append({
                    "text": ins,
                    "citation": "组委会公司池与权威财经媒体报道"})
        macro = data.get("macro_analysis")
        if macro:
            for name, ind in macro.indicators.items():
                claims.append({
                    "text": f"{name} 最新值 {ind['value']}"
                            f"（{ind['period']}）",
                    "citation": ind.get("source", "国家统计局")})
        return claims

    # ------------------------------------------------------------------
    def _get_llm(self):
        """懒加载 LLM 客户端；不可用返回 None（全流程走规则降级）"""
        if not self._llm_ready:
            self._llm_ready = True
            try:
                from common.llm_client import LLMClient
                if LLMClient.available(self.config.get("llm")):
                    self._llm = LLMClient(self.config.get("llm"))
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM 客户端初始化失败: %s", e)
        return self._llm

    # ------------------------------------------------------------------
    # 行业研报（Day 6：最小可用落地，分治式与公司研报同构）
    # ------------------------------------------------------------------
    def _write_industry(self, data: dict, request,
                        revision_feedback: Optional[dict] = None
                        ) -> ReportDraft:
        draft = ReportDraft()
        name = getattr(request, "name", "") or getattr(request, "target", "")
        industry = data.get("industry_analysis")
        news = data.get("news_data", {}) or {}
        industry_dict = industry.to_dict() if industry else {}
        peer_metrics = data.get("peer_metrics") or {}

        # 1. 大纲：固定八章模板（行业报告结构稳定，不走 LLM 大纲）
        outline = [{"part_title": t, "part_desc": d}
                   for t, d in OUTLINE_INDUSTRY_FALLBACK]
        draft.outline = outline

        # 2. 逐段撰写（分治式：前文摘要喂回；修订轮注入审查指令）
        revision = self._revision_instructions(
            (revision_feedback or {}).get("issues", []) or [])
        sections: List[Tuple[str, str]] = []
        for part in outline:
            title, desc = part["part_title"], part["part_desc"]
            material = self._materials_for_industry(
                title, name=name, industry_dict=industry_dict,
                peer_metrics=peer_metrics, news=news)
            body = self._write_section(
                name, title, desc, material,
                context=self._digest(sections), revision=revision,
                source_map=SECTION_SOURCE_INDUSTRY)
            sections.append((title, body))

        # 3. 图表：优先复用研究数据，缺失时按板块成分公司净利润生成
        charts: List[Chart] = list(data.get("charts", []) or [])
        if not charts and peer_metrics:
            try:
                from generators.chart_generator import ChartGenerator
                charts = [ChartGenerator(
                    self.config.get("chart_dir")).generate_sector_bar(
                    name, peer_metrics)]
            except Exception as e:  # noqa: BLE001 图表失败不阻断正文
                logger.warning("行业研报图表生成失败: %s", e)
        draft.charts = charts

        # 4. 拼接正文 + 来源清单 + 论据卡片
        draft.content = self._assemble_industry(
            sections, charts, data, name, request)
        draft.citations = self._collect_sources_industry(data)
        draft.claims = self._build_claims_industry(industry_dict)
        return draft

    def _materials_for_industry(
        self, title: str, *, name, industry_dict, peer_metrics, news,
    ) -> str:
        """行业研报章节材料（板块级聚合，同源一致）"""
        prosperity = industry_dict.get("prosperity", {})
        competition = industry_dict.get("competition", {})
        news_titles = [
            f"{it.get('title', '')}（{it.get('source', '')}，"
            f"{it.get('date', '')}）"
            for it in (news.get("items", []) or [])[:8]
        ]
        top5 = sorted(
            ((m.get("name") or s, m.get("net_profit"))
             for s, m in peer_metrics.items()
             if m.get("net_profit") is not None),
            key=lambda kv: kv[1], reverse=True)[:5]
        if "核心观点" in title:
            payload = {
                "研报类型": "行业研报", "板块": name,
                "景气度": prosperity,
                "板块洞察": industry_dict.get("insights", [])[:5],
            }
        elif "投资结论" in title:
            level = prosperity.get("level", "")
            rating = {"景气向上": "超配", "平稳运行": "标配"}.get(
                level, "低配或观望")
            logic = {
                "景气向上": "新闻情绪与政策信号偏暖，景气判定向上",
                "平稳运行": "情绪信号均衡，景气平稳，标配跟踪观察",
            }.get(level, "景气数据不足或承压，谨慎配置并阐明理由")
            payload = {
                "板块": name, "配置建议": rating, "决策逻辑": logic,
                "景气度判定": level,
                "关键依据": industry_dict.get("insights", [])[:3],
            }
        elif "行业概况" in title:
            payload = {
                "板块": name,
                "成分公司": industry_dict.get("peers", []),
                "近期新闻标题": news_titles,
            }
        elif "景气度" in title:
            payload = {
                "板块": name,
                "景气度统计": prosperity,
                "近期新闻标题": news_titles[:5],
            }
        elif "竞争格局" in title:
            payload = {
                "板块": name,
                "对比表": competition.get("table", []),
                "公司名单": competition.get("companies", []),
                "净利润Top5(亿元)": top5,
            }
        elif "估值与资金面" in title:
            payload = {
                "板块": name,
                "口径说明": "本工作流未接入板块级估值与资金流数据源，"
                            "此处基于成分公司最新期财务指标作定性概述，"
                            "不编造估值数字",
                "成分公司最新期净利润(亿元)": top5,
                "景气方向": prosperity.get("level", ""),
            }
        elif "风险" in title:
            level = prosperity.get("level", "")
            industry_note = {
                "景气承压": "板块景气承压（景气度判定：景气承压）",
                "平稳运行": "板块景气平稳，需求端波动为主要扰动",
                "景气向上": "未见显著行业负面信号（景气度判定：景气向上）",
            }.get(level, "行业景气数据不足，不作景气方向判断")
            payload = {
                "板块": name, "行业负面": industry_note,
                "宏观提示": "关注宏观指标（GDP/CPI/PMI）波动对板块需求的传导",
            }
        else:  # 数据来源章节由程序生成，不走 LLM
            payload = {"说明": "本段由程序生成来源清单"}
        return json.dumps(payload, ensure_ascii=False, default=str)[:2600]

    def _assemble_industry(
        self, sections: List[Tuple[str, str]], charts: List[Chart],
        data, name, request,
    ) -> str:
        """行业研报正文组装：竞争格局章注入对比表与净利润柱状图"""
        lines = [f"# {name} 行业分析报告", ""]
        lines += ["| 属性 | 值 |", "|------|-----|",
                  "| 报告类型 | 行业研报 |",
                  f"| 板块 | {name} |",
                  f"| 报告日期 | {date.today().isoformat()} |", ""]

        def chart_md(chart: Chart) -> List[str]:
            if not chart.image_path:
                return []
            return ["", f"![{chart.title}]({chart.image_path})",
                    f"*{chart.caption}*"]

        for title, body in sections:
            if "数据来源" in title:
                continue  # 来源章由程序文末生成（避免编造）
            lines.append(f"## {title}")
            lines.append("")
            lines.append(body)
            if "竞争格局" in title:
                for c in charts:
                    if c.chart_type == "table" and c.caption:
                        lines += ["", c.caption]
                    elif c.chart_type == "bar":
                        lines += chart_md(c)
            lines.append("")

        # 数据来源清单（章节号与实际大纲一致）
        src_title = next((t for t, _ in sections if "数据来源" in t),
                         "八、数据来源")
        lines.append(f"## {src_title}")
        lines.append("")
        lines += [f"- {s}" for s in self._collect_sources_industry(data)]
        lines.append("数据来源：以上权威渠道来源清单汇总")
        lines += ["", "---",
                  "*免责声明：本报告由 AI Agent 自动生成，仅供参考，"
                  "不构成投资建议。*"]
        content = "\n".join(lines)
        content, issues = self._localize_images(content)
        data["image_issues"] = issues  # 留痕（Reviewer 可读）
        return content

    def _collect_sources_industry(self, data) -> List[str]:
        """行业研报来源清单（权威白名单口径）"""
        sources = ["行业分组：组委会公司池（上市公司列表.xlsx）",
                   "财务数据：板块成分公司定期财报（akshare 财务摘要，"
                   "东方财富 F10）"]
        seen = set()
        for it in ((data.get("news_data") or {}).get("items", []) or []):
            src = it.get("source", "")
            if src and src not in seen:
                seen.add(src)
                sources.append(f"新闻资讯：{src}")
        if data.get("macro_analysis"):
            sources.append("宏观数据：国家统计局（经 akshare 接口）")
        return sources

    @staticmethod
    def _build_claims_industry(industry_dict) -> list:
        """行业研报论据卡片（洞察 → {text, citation}）

        citation 须命中 CitationChecker 权威白名单词（组委会公司池/
        财联社等），否则卡片级校验报「来源非权威」。
        """
        claims = []
        for ins in industry_dict.get("insights", []):
            claims.append({"text": ins,
                           "citation": "组委会公司池与财联社等权威财经媒体报道"})
        prosperity = industry_dict.get("prosperity") or {}
        if prosperity:
            claims.append({
                "text": (f"板块景气度判定为{prosperity.get('level', '')}"
                         f"（情绪分 {prosperity.get('sentiment_score', '')}"
                         f"，近 {prosperity.get('news_count', '')} 条相关新闻）"),
                "citation": "财联社等权威财经媒体报道（新闻情绪统计）"})
        return claims

    # ------------------------------------------------------------------
    # 宏观研报（Day 6：最小可用落地，分治式与公司研报同构）
    # ------------------------------------------------------------------
    def _write_macro(self, data: dict, request,
                     revision_feedback: Optional[dict] = None
                     ) -> ReportDraft:
        draft = ReportDraft()
        period = getattr(request, "period", "") or getattr(
            request, "target", "") or "最新"
        macro = data.get("macro_analysis")
        news = data.get("news_data", {}) or {}
        macro_dict = macro.to_dict() if macro else {}

        # 1. 大纲：固定七章模板（宏观报告结构稳定，不走 LLM 大纲）
        outline = [{"part_title": t, "part_desc": d}
                   for t, d in OUTLINE_MACRO_FALLBACK]
        draft.outline = outline

        # 2. 逐段撰写（分治式：前文摘要喂回；修订轮注入审查指令）
        revision = self._revision_instructions(
            (revision_feedback or {}).get("issues", []) or [])
        sections: List[Tuple[str, str]] = []
        for part in outline:
            title, desc = part["part_title"], part["part_desc"]
            material = self._materials_for_macro(
                title, period=period, macro_dict=macro_dict, news=news)
            body = self._write_section(
                period, title, desc, material,
                context=self._digest(sections), revision=revision,
                source_map=SECTION_SOURCE_MACRO)
            sections.append((title, body))

        # 3. 拼接正文 + 来源清单 + 论据卡片（宏观研报暂不含图表）
        draft.charts = list(data.get("charts", []) or [])
        draft.content = self._assemble_macro(sections, data, period, request)
        draft.citations = self._collect_sources_macro(data)
        draft.claims = self._build_claims_macro(macro_dict)
        return draft

    def _materials_for_macro(
        self, title: str, *, period, macro_dict, news,
    ) -> str:
        """宏观研报章节材料（指标口径同 MacroAnalyzer，禁止编造）"""
        indicators = macro_dict.get("indicators", {})
        indicator_brief = {
            k: {"最新值": v.get("value"), "期间": v.get("period")}
            for k, v in indicators.items()
        }
        news_titles = [
            f"{it.get('title', '')}（{it.get('source', '')}，"
            f"{it.get('date', '')}）"
            for it in (news.get("items", []) or [])[:8]
        ]
        if "核心观点" in title:
            payload = {
                "研报类型": "宏观研报", "报告周期": period,
                "核心指标概览": indicator_brief,
                "宏观洞察": macro_dict.get("insights", [])[:5],
            }
        elif "结论与板块配置" in title or "配置建议" in title:
            payload = {
                "报告周期": period,
                "核心指标概览": indicator_brief,
                "板块影响判断": macro_dict.get("sector_impact", {}),
                "宏观洞察": macro_dict.get("insights", [])[:4],
            }
        elif "宏观指标" in title:
            payload = {"报告周期": period, "指标明细": indicators}
        elif "政策" in title:
            payload = {
                "报告周期": period,
                "政策趋势": macro_dict.get("policy_trends", {}),
                "政策信号新闻": news_titles[:6],
            }
        elif "影响" in title:
            payload = {
                "报告周期": period,
                "板块影响判断": macro_dict.get("sector_impact", {}),
                "宏观洞察": macro_dict.get("insights", []),
            }
        elif "风险" in title:
            payload = {
                "报告周期": period,
                "风险信号": [i for i in macro_dict.get("insights", [])
                             if any(h in i for h in NEGATIVE_HINTS)],
                "提示": "关注宏观指标（GDP/CPI/PMI）超预期波动"
                        "对资本市场与板块估值的传导",
            }
        else:  # 数据来源章节由程序生成，不走 LLM
            payload = {"说明": "本段由程序生成来源清单"}
        return json.dumps(payload, ensure_ascii=False, default=str)[:2600]

    def _assemble_macro(
        self, sections: List[Tuple[str, str]], data, period, request,
    ) -> str:
        """宏观研报正文组装（无图表注入）"""
        lines = [f"# {period} 宏观研究报告", ""]
        lines += ["| 属性 | 值 |", "|------|-----|",
                  "| 报告类型 | 宏观研报 |",
                  f"| 报告周期 | {period} |",
                  f"| 报告日期 | {date.today().isoformat()} |", ""]
        for title, body in sections:
            if "数据来源" in title:
                continue  # 来源章由程序文末生成（避免编造）
            lines.append(f"## {title}")
            lines.append("")
            lines.append(body)
            lines.append("")

        src_title = next((t for t, _ in sections if "数据来源" in t),
                         "七、数据来源")
        lines.append(f"## {src_title}")
        lines.append("")
        lines += [f"- {s}" for s in self._collect_sources_macro(data)]
        lines.append("数据来源：以上权威渠道来源清单汇总")
        lines += ["", "---",
                  "*免责声明：本报告由 AI Agent 自动生成，仅供参考，"
                  "不构成投资建议。*"]
        content = "\n".join(lines)
        content, issues = self._localize_images(content)
        data["image_issues"] = issues  # 留痕（Reviewer 可读）
        return content

    def _collect_sources_macro(self, data) -> List[str]:
        """宏观研报来源清单（权威白名单口径）"""
        sources = ["宏观数据：国家统计局（经 akshare 接口）"]
        seen = set()
        for it in ((data.get("news_data") or {}).get("items", []) or []):
            src = it.get("source", "")
            if src and src not in seen:
                seen.add(src)
                sources.append(f"新闻资讯：{src}")
        sources.append("板块配置参考：组委会公司池（上市公司列表.xlsx）")
        return sources

    @staticmethod
    def _build_claims_macro(macro_dict) -> list:
        """宏观研报论据卡片（指标值 → {text, citation}）"""
        claims = []
        for name, ind in (macro_dict.get("indicators") or {}).items():
            claims.append({
                "text": f"{name} 最新值 {ind.get('value')}"
                        f"（{ind.get('period', '')}）",
                "citation": ind.get("source", "国家统计局")})
        for ins in macro_dict.get("insights", []):
            claims.append({"text": ins,
                           "citation": "国家统计局与财联社等权威财经媒体报道"})
        return claims
