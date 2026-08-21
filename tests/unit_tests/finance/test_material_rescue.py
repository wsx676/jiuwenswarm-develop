# -*- coding: utf-8 -*-
"""MaterialRescue 单元测试：材料三维评估与补救循环（方案 3）

覆盖：三维评估（相关性/完整性/时效性）/ 缓存补采 / RAG 精读 /
实体锚定 / 缺失原因标注 / 遥测留痕 / ReportWriter 接线与默认开关口径。
"""

import json
from datetime import date, timedelta

from common.telemetry import RunStats
from generators.material_rescue import AssessResult, MaterialRescue
from generators.report_writer import ReportWriter

ENABLED = {"material_rescue": {"enabled": True}}
FRESH_DATE = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
STALE_DATE = (date.today() - timedelta(days=200)).strftime("%Y-%m-%d")


def _news(items):
    return {"keyword": "贵州茅台", "count": len(items), "items": items}


def _item(title, dt):
    return {"title": title, "source": "财联社", "url": "http://a.com/x",
            "date": dt}


def _rescue(entities=("贵州茅台",), config=ENABLED, stats=None):
    return MaterialRescue(config, entities=list(entities),
                          stats=stats or RunStats())


class TestAssess:
    def test_all_pass(self):
        r = _rescue()
        res = r.assess("三、公司概况",
                       {"公司": "贵州茅台", "近期新闻标题": ["茅台动销"]},
                       "company", _news([_item("茅台", FRESH_DATE)]))
        assert res.ok and not res.missing and not res.stale_note
        assert res.entity_hits == 1

    def test_entity_not_mentioned(self):
        r = _rescue()
        res = r.assess("三、公司概况", {"近期新闻标题": ["某酒业动销"]},
                       "company", None)
        assert not res.ok
        assert "目标实体未提及" in res.missing

    def test_no_entities_skips_relevance(self):
        r = _rescue(entities=())
        res = r.assess("三、公司概况", {"近期新闻标题": ["任意"]},
                       "company", None)
        assert res.ok

    def test_completeness_missing_field(self):
        r = _rescue()
        res = r.assess("五、财务分析", {"公司": "贵州茅台", "财务指标": {}},
                       "company", None)
        assert not res.ok
        assert "财务指标" in res.missing

    def test_completeness_nested_path(self):
        r = _rescue()
        res = r.assess("四、行业分析",
                       {"公司": "贵州茅台", "景气度": {"level": ""}},
                       "company", None)
        assert not res.ok and "level" in res.missing

    def test_required_fields_by_report_type(self):
        # 行业研报的行业概况要求成分公司；公司研报同章名要求景气度
        r = _rescue()
        res_ind = r.assess("三、行业概况", {"板块": "贵州茅台"}, "industry",
                           None)
        assert "成分公司" in res_ind.missing

    def test_stale_news_note_only(self):
        # 时效性仅提示不阻断：材料齐全但新闻超阈值
        r = _rescue()
        res = r.assess("三、公司概况",
                       {"公司": "贵州茅台", "近期新闻标题": ["茅台动销"]},
                       "company", _news([_item("茅台", STALE_DATE)]))
        assert res.ok
        assert "天" in res.stale_note and "阈值" in res.stale_note

    def test_unparseable_date_not_stale(self):
        r = _rescue()
        res = r.assess("三、公司概况",
                       {"公司": "贵州茅台", "近期新闻标题": ["茅台动销"]},
                       "company", _news([_item("茅台", "未知日期")]))
        assert res.ok and not res.stale_note


class TestRescue:
    def test_cache_refill_news_titles(self):
        stats = RunStats()
        r = _rescue(stats=stats)
        payload = {"公司": "贵州茅台"}
        data = {"news_data": _news([_item("茅台动销稳健", FRESH_DATE)])}
        res = r.assess("三、公司概况", payload, "company", data["news_data"])
        assert not res.ok
        out = r.rescue("三、公司概况", payload, data, "company", res)
        assert out["近期新闻标题"]              # 缓存补采生效
        assert any("缓存补采" in m for m in res.rescued_by)
        assert res.ok                           # 复评通过
        assert "数据缺失原因" not in out
        assert stats.material_rescue == {"failed": 1, "rescued": 1,
                                         "degraded": 0}

    def test_cache_refill_prosperity_level(self):
        class _Industry:
            def to_dict(self):
                return {"prosperity": {"level": "平稳运行",
                                       "news_count": 12},
                        "peers": ["贵州茅台", "五粮液"]}
        r = _rescue()
        payload = {"公司": "贵州茅台", "景气度": {}}
        data = {"industry_analysis": _Industry()}
        res = r.assess("四、行业分析", payload, "company", None)
        assert not res.ok
        out = r.rescue("四、行业分析", payload, data, "company", res)
        assert out["景气度"]["level"] == "平稳运行"
        assert res.ok

    def test_rag_full_text_only_on_failure(self):
        chunks = [{"content": "方法论A" * 10}, {"content": "方法论B"}]
        r = _rescue()
        # 评估失败 + 有知识库 → 注入全文
        res = r.assess("五、财务分析", {"公司": "贵州茅台"}, "company", None)
        out = r.rescue("五、财务分析", {"公司": "贵州茅台"},
                       {"knowledge_chunks": chunks}, "company", res)
        assert any("方法论精读" in k for k in out)
        # 仅时效提示（评估通过）→ 不注入全文
        r2 = _rescue()
        payload = {"公司": "贵州茅台", "财务指标": {"roe": 0.3}}
        res2 = r2.assess("五、财务分析", payload, "company",
                         _news([_item("茅台", STALE_DATE)]))
        assert res2.ok and res2.stale_note
        # 与 writer 接线一致：rescue 收到完整 research_data（含新闻）
        out2 = r2.rescue(
            "五、财务分析", payload,
            {"knowledge_chunks": chunks,
             "news_data": _news([_item("茅台", STALE_DATE)])},
            "company", res2)
        assert not any("方法论精读" in k for k in out2)
        assert out2["数据时效提示"]

    def test_entity_anchor(self):
        r = _rescue()
        payload = {"近期新闻标题": ["某酒业动销"]}
        res = r.assess("三、公司概况", payload, "company", None)
        out = r.rescue("三、公司概况", payload, {}, "company", res)
        assert out["研究对象"] == "贵州茅台"
        assert "实体锚定" in res.rescued_by

    def test_still_missing_marks_reason(self):
        stats = RunStats()
        r = _rescue(stats=stats)
        payload = {"说明": "无数据"}          # 无实体 + 必需字段缺失
        res = r.assess("五、财务分析", payload, "company", None)
        out = r.rescue("五、财务分析", payload, {}, "company", res)
        assert not res.ok
        assert "数据缺失原因" in out
        assert "财务指标" in out["数据缺失原因"]
        assert stats.material_rescue == {"failed": 1, "rescued": 0,
                                         "degraded": 1}

    def test_original_payload_untouched(self):
        r = _rescue()
        payload = {"公司": "贵州茅台"}
        r.rescue("三、公司概况", payload, {"news_data": _news(
            [_item("茅台", FRESH_DATE)])}, "company",
            r.assess("三、公司概况", payload, "company", None))
        assert payload == {"公司": "贵州茅台"}


class TestTelemetry:
    def test_add_material_rescue_counters(self):
        stats = RunStats()
        stats.add_material_rescue(rescued=True)
        stats.add_material_rescue(rescued=False)
        assert stats.material_rescue == {"failed": 2, "rescued": 1,
                                         "degraded": 1}

    def test_summary_conditional_output(self):
        stats = RunStats()
        assert "material_rescue" not in stats.summary()
        stats.add_material_rescue(rescued=False)
        assert stats.summary()["material_rescue"]["degraded"] == 1


class TestWriterWiring:
    def _writer(self, config):
        return ReportWriter(config)

    def test_disabled_returns_unchanged(self):
        w = self._writer({})
        material = '{"公司": "无关内容"}'
        out = w._assess_and_rescue("三、公司概况", material, {}, ["贵州茅台"],
                                   "company")
        assert out == material

    def test_source_section_skipped(self):
        w = self._writer(ENABLED)
        material = '{"说明": "本段由程序生成来源清单"}'
        out = w._assess_and_rescue("八、数据来源", material, {}, ["贵州茅台"],
                                   "company")
        assert out == material

    def test_non_json_returns_unchanged(self):
        w = self._writer(ENABLED)
        assert w._assess_and_rescue("三、公司概况", "非JSON材料", {},
                                    ["贵州茅台"], "company") == "非JSON材料"

    def test_ok_material_unchanged(self):
        w = self._writer(ENABLED)
        material = json.dumps({"公司": "贵州茅台",
                               "近期新闻标题": ["茅台动销"]},
                              ensure_ascii=False)
        data = {"news_data": _news([_item("茅台", FRESH_DATE)])}
        assert w._assess_and_rescue("三、公司概况", material, data,
                                    ["贵州茅台"], "company") == material

    def test_rescue_enriches_material(self):
        w = self._writer(ENABLED)
        material = json.dumps({"说明": "无数据"}, ensure_ascii=False)
        data = {"news_data": _news([_item("茅台业绩稳健", FRESH_DATE)])}
        out = w._assess_and_rescue("三、公司概况", material, data,
                                   ["贵州茅台"], "company")
        payload = json.loads(out)
        assert payload["近期新闻标题"]          # 缓存补采
        assert payload["研究对象"] == "贵州茅台"  # 实体锚定

    def test_rescue_degraded_marks_reason(self):
        w = self._writer(ENABLED)
        material = json.dumps({"说明": "无数据"}, ensure_ascii=False)
        out = w._assess_and_rescue("五、财务分析", material, {},
                                   ["贵州茅台"], "company")
        payload = json.loads(out)
        assert "数据缺失原因" in payload
