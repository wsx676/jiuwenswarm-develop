# -*- coding: utf-8 -*-
"""PlannerAgent/ResearcherAgent Day 4 回归测试

覆盖：
- Planner：三类研报的子任务拆解（company 含 rag/peer_filing，
  industry/macro 仅 news+rag）
- Researcher：collect_tasks 计划驱动采集、RAG 检索注入、
  来源清单构建、supplement 定向补采（只补缺口不全量重采）

全部离线（monkeypatch 采集层，不发网络请求）。
"""

from types import SimpleNamespace

from agents.planner import PlannerAgent
from agents.researcher import ResearcherAgent

QUOTE = {"symbol": "600519", "name": "贵州茅台", "source": "mock行情源",
         "collected_at": "2026-08-18T10:00:00", "latest_close": 1286.0,
         "market_cap": 16164.0, "total_shares": 12.56, "records": []}
FILING = {"symbol": "600519", "source": "mock财报源",
          "collected_at": "2026-08-18T10:00:00", "statements": []}
NEWS = {"keyword": "贵州茅台", "source": "", "items": [
    {"title": "x", "source": "财联社", "url": "", "summary": ""}]}
CHUNK = {"content": "PE 相对估值适用于盈利稳定的消费龙头",
         "source": "估值方法.md", "score": 0.3,
         "heading": "估值方法 > 相对估值"}


def _offline_researcher(tmp_path, monkeypatch):
    """ResearcherAgent + 三采集全部 mock（隔离网络与真实数据目录）"""
    monkeypatch.setattr(
        ResearcherAgent, "_collect_quote",
        staticmethod(lambda s, n: dict(QUOTE)))
    monkeypatch.setattr(
        ResearcherAgent, "_collect_filing",
        staticmethod(lambda s: dict(FILING)))
    monkeypatch.setattr(
        ResearcherAgent, "_collect_news",
        staticmethod(lambda k: dict(NEWS)))
    return ResearcherAgent({"data_dir": str(tmp_path / "data"),
                            "chart_dir": str(tmp_path / "charts")})


class TestPlannerDecompose:
    def test_company_plan_includes_rag_and_peer_filing(self):
        plan = PlannerAgent({}).plan(SimpleNamespace(
            report_type="company", target="600519", name="贵州茅台"))
        assert "rag" in plan["collect_tasks"]
        assert "peer_filing" in plan["collect_tasks"]
        assert plan["sector"] == "消费板块"

    def test_industry_plan_news_and_rag_only(self):
        plan = PlannerAgent({}).plan(SimpleNamespace(
            report_type="industry", target="白酒", name="白酒"))
        assert plan["collect_tasks"] == ["news", "rag"]
        assert "finance" not in plan["analyze_tasks"]

    def test_macro_plan_only_macro_analysis(self):
        plan = PlannerAgent({}).plan(SimpleNamespace(
            report_type="macro", target="2026Q2", name=""))
        assert plan["collect_tasks"] == ["news", "rag"]
        assert plan["analyze_tasks"] == ["macro"]


class TestResearchPlanDriven:
    def test_collect_only_planned_tasks(self, tmp_path, monkeypatch):
        """计划驱动：industry 计划不含 quote/filing → 行情财报零采集"""
        calls = []
        monkeypatch.setattr(
            ResearcherAgent, "_collect_quote",
            staticmethod(lambda s, n: calls.append("quote") or dict(QUOTE)))
        monkeypatch.setattr(
            ResearcherAgent, "_collect_filing",
            staticmethod(lambda s: calls.append("filing") or dict(FILING)))
        monkeypatch.setattr(
            ResearcherAgent, "_collect_news",
            staticmethod(lambda k: dict(NEWS)))
        monkeypatch.setattr(
            ResearcherAgent, "_retrieve_knowledge",
            lambda self, plan: [dict(CHUNK)])
        data = ResearcherAgent(
            {"data_dir": str(tmp_path / "data")}).research(
            {"report_type": "industry", "target": "白酒",
             "collect_tasks": ["news", "rag"],
             "analyze_tasks": []})   # 不跑分析引擎（隔离宏观网络请求）
        assert calls == []                    # quote/filing 未被采集
        assert data["quote_data"] == {}
        assert data["filing_data"] == {}
        assert data["news_data"]["items"]
        assert data["knowledge_chunks"] == [CHUNK]

    def test_company_plan_collects_all_and_cites(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            ResearcherAgent, "_retrieve_knowledge",
            lambda self, plan: [dict(CHUNK)])
        data = _offline_researcher(tmp_path, monkeypatch).research(
            {"report_type": "company", "target": "600519",
             "name": "贵州茅台", "sector": "消费板块",
             "collect_tasks": ["quote", "filing", "news", "rag"],
             "pool": {}, "competitors": []})
        assert data["quote_data"]["market_cap"] == 16164.0
        assert data["knowledge_chunks"] == [CHUNK]
        # 来源清单：行情/财报/新闻源 + 知识库（溯源闭环）
        cites = "\n".join(data["citations"])
        assert "mock行情源" in cites
        assert "mock财报源" in cites
        assert "财联社" in cites
        assert "财务方法论知识库" in cites

    def test_rag_failure_degrades_empty(self, tmp_path, monkeypatch):
        """RAG 检索失败降级空知识，不阻断主链路"""
        monkeypatch.setattr(
            ResearcherAgent, "_retrieve_knowledge",
            lambda self, plan: (_ for _ in ()).throw(RuntimeError("kb down")))
        data = _offline_researcher(tmp_path, monkeypatch).research(
            {"report_type": "company", "target": "600519",
             "name": "贵州茅台",
             "collect_tasks": ["quote", "filing", "news", "rag"],
             "pool": {}, "competitors": []})
        assert data["quote_data"]             # 主链路不受影响


class TestSupplement:
    def test_valuation_gap_triggers_quote_recollect(self, tmp_path,
                                                    monkeypatch):
        """估值缺口（市值缺失）→ 重采行情补齐并重跑财务分析"""
        researcher = _offline_researcher(tmp_path, monkeypatch)
        stale = dict(QUOTE, market_cap=None)
        data = researcher.supplement(
            {"quote_data": stale, "filing_data": FILING},
            {"issues": ["估值材料缺失：PE 不可算"]})
        assert data["quote_data"]["market_cap"] == 16164.0
        assert any("补齐市值" in l for l in data["supplement_log"])

    def test_writer_side_issues_no_recollect(self, tmp_path, monkeypatch):
        """引用率/结构类问题属 Writer 职责：留痕不重采"""
        calls = []
        researcher = _offline_researcher(tmp_path, monkeypatch)
        monkeypatch.setattr(
            ResearcherAgent, "_collect_quote",
            staticmethod(lambda s, n: calls.append(1) or dict(QUOTE)))
        data = researcher.supplement(
            {"quote_data": dict(QUOTE), "filing_data": FILING},
            {"issues": ["正文数据句引用率 80% 低于 90% 闸门"]})
        assert calls == []
        assert any("无数据缺口不重采" in l
                   for l in data["supplement_log"])
        assert data["quote_data"] == QUOTE    # 原数据不动

    def test_no_issues_returns_untouched(self, tmp_path, monkeypatch):
        researcher = _offline_researcher(tmp_path, monkeypatch)
        origin = {"quote_data": dict(QUOTE)}
        data = researcher.supplement(origin, {"issues": []})
        assert data["quote_data"] == QUOTE
