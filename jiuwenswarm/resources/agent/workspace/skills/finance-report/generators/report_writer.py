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
    def write(self, research_data: dict, request) -> ReportDraft:
        draft = ReportDraft()
        report_type = getattr(request, "report_type", "company")
        if report_type != "company":
            # 行业/宏观研报 Day 3 后续版本扩展；当前返回占位骨架
            draft.content = f"# {getattr(request, 'name', '')}研报\n\n（待生成）"
            return draft
        return self._write_company(research_data, request)

    # ------------------------------------------------------------------
    def _write_company(self, data: dict, request) -> ReportDraft:
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

        # 2. 逐段撰写（分治式：前文摘要喂回）
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
                context=self._digest(sections))
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
        material: str, context: str,
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
        return self._template_section(title, material)

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

    def _template_section(self, title: str, material: str) -> str:
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
        src = SECTION_SOURCE.get(title)
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

    # 占位：行业/宏观研报（Day 3+ 迭代）
    def _write_industry(self, data: dict, request) -> ReportDraft:
        return ReportDraft(content=f"# {request.name}行业研报\n\n（待生成）")

    def _write_macro(self, data: dict, request) -> ReportDraft:
        return ReportDraft(content=f"# {request.period}宏观研报\n\n（待生成）")
