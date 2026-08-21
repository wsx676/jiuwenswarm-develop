# -*- coding: utf-8 -*-
"""finance-report e2e：产物断言（共用 e2e_pipeline 一次运行）

实现依据：docs/plans/2026-08-20-finance-report-e2e-design.md §三（v2）。
"""

import json

import pytest

from .conftest import OUTPUT_DIR, POOL_FILE, run_stage

pytestmark = pytest.mark.e2e


def _load(path):
    return json.loads((OUTPUT_DIR / path).read_text(encoding="utf-8"))


class TestPool:
    def test_pool_json(self, e2e_pipeline, e2e_env):
        """公司池校验：解析 JSON 断言，不匹配子串（M7）"""
        proc = run_stage(["pool"], env=e2e_env, timeout=120)
        payload = json.loads(proc.stdout[proc.stdout.index("{"):])
        assert payload["total"] >= 30
        assert len(payload["sectors"]) >= 5


class TestDataCache:
    def test_data_files(self, e2e_pipeline):
        """采集缓存 ≥ 100 个 JSON（49 标的 × 3 类；混台=缓存优先）"""
        assert len(list((OUTPUT_DIR / "data").glob("*.json"))) >= 100


class TestScoresCache:
    def test_scores(self, e2e_pipeline):
        cache = _load("decision_log/scores_cache.json")
        assert len(cache.get("scores", {})) >= 30


class TestPortfolio:
    def test_portfolio_invariants(self, e2e_pipeline):
        """白名单 / 单标权重 / 总权重（与 validate_portfolio 同口径）"""
        from collectors.pool_loader import load_pool, whitelist_symbols
        allowed = whitelist_symbols(load_pool(str(POOL_FILE)))
        portfolio = _load("Portfolio.json")
        assert isinstance(portfolio, dict) and portfolio
        total = 0.0
        for symbol, weight in portfolio.items():
            assert symbol in allowed, f"{symbol} 不在公司池白名单"
            assert 0 < weight <= 0.4 + 1e-9, f"{symbol} 权重 {weight} 超限"
            total += weight
        assert 0 < total <= 1.0 + 1e-9


class TestDecision:
    def test_position_stance(self, e2e_pipeline):
        decision = _load("decision_log/decision.json")
        assert decision["position_decision"] in ("full", "partial", "empty")
        assert decision["position_rationale"].strip()

    def test_determinism(self, e2e_pipeline):
        """确定性：两次 invest 的 Portfolio.json 字节级一致（M5 + D1 时序修正）"""
        first = (OUTPUT_DIR / "decision_log" / "portfolio_run1.json").read_bytes()
        assert first == (OUTPUT_DIR / "Portfolio.json").read_bytes(), \
            "同一评分缓存两次决策结果不一致，破坏可复现性"


class TestReports:
    def test_three_report_types(self, e2e_pipeline):
        """三类研报齐全 + 章节合规 + 无 LLM 降级标记（C2 防护）"""
        company = list((OUTPUT_DIR / "个股投资研报").glob("*.md"))
        industry = list((OUTPUT_DIR / "行业研报").glob("*.md"))
        macro = list((OUTPUT_DIR / "宏观研报").glob("*.md"))
        assert company and industry and macro, "三类研报须齐全"

        comp = company[0].read_text(encoding="utf-8")
        assert "核心观点" in comp and "投资结论" in comp and "风险提示" in comp
        assert comp.count("数据来源") >= 3 and "免责声明" in comp
        assert len(comp) >= 800

        ind = industry[0].read_text(encoding="utf-8")
        assert "板块核心观点" in ind and "竞争格局" in ind and "免责声明" in ind

        mac = macro[0].read_text(encoding="utf-8")
        assert "宏观核心观点" in mac and "风险提示" in mac and "免责声明" in mac

        # LLM 降级防护：任一研报含模板段标记即 fail（Key 失效/接口变更）
        for text in (comp, ind, mac):
            assert "规则模板段" not in text, "报告走了规则降级（LLM 未生效）"


class TestRunStats:
    def test_run_stats(self, e2e_pipeline):
        stats = _load("decision_log/run_stats.json")
        assert all(r["seed"] == 20260819 for r in stats["runs"])
        recent = stats["runs"][-5:]
        # D2 修正：latest run 是第二次独立 invest（phase 少），
        # 断言近 5 次 run 中存在完整链路（phases ≥ 4）
        assert any(len(r.get("phases", [])) >= 4 for r in recent)
        # LLM 真实调用留痕（C2 防护）
        assert any(r["llm"]["calls"] > 0 for r in recent)
