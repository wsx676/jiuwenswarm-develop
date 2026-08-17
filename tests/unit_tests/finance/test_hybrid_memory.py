# -*- coding: utf-8 -*-
"""混合记忆管理单元测试：表格压缩、短期预算逐出、长期摘要沉淀、
跨标的上下文构建（全部本地，无网络请求）"""

import json
import os

import pytest

from common.hybrid_memory import (CompanySummary, HybridMemory, LongTermMemory,
                                  ShortTermMemory, compress_table)


class TestCompressTable:
    def test_big_table_keeps_head_and_tail(self):
        rows = [["日期", "收盘价"]] + [[f"2026-01-{i:02d}", 100 + i]
                                       for i in range(1, 251)]  # 251 行
        text = compress_table(rows)
        lines = text.split("\n")
        assert lines[0] == "日期 | 收盘价"          # 表头
        assert lines[1] == "2026-01-01 | 101"      # 前 5 行（含表头）
        assert lines[4] == "2026-01-04 | 104"
        assert "省略 241 行，共 251 行" in lines[5]  # 省略提示
        assert lines[6] == "2026-01-246 | 346"     # 后 5 行起始
        assert lines[-1] == "2026-01-250 | 350"
        assert len(lines) == 5 + 1 + 5              # 头5 + 提示 + 尾5

    def test_small_table_not_compressed(self):
        rows = [["a", "b"], ["1", "2"], ["3", "4"]]
        text = compress_table(rows)
        assert "省略" not in text
        assert text.count("\n") == 2

    def test_empty_table(self):
        assert compress_table([]) == ""

    def test_none_cell_rendered_blank(self):
        text = compress_table([["x", None]])
        assert text == "x | "


class TestShortTermMemory:
    def test_fifo_eviction_on_budget(self):
        stm = ShortTermMemory(budget=100)
        stm.add("行情", "A" * 60)
        assert stm.evicted == 0
        stm.add("财报", "B" * 60)  # 超限，逐出最旧的"行情"
        assert stm.evicted == 1
        assert stm.keys == ["财报"]
        assert stm.size <= stm.budget

    def test_same_key_overwrites(self):
        stm = ShortTermMemory(budget=1000)
        stm.add("k", "old")
        stm.add("k", "new")
        assert stm.keys == ["k"]
        assert "new" in stm.render() and "old" not in stm.render()

    def test_render_has_section_headers(self):
        stm = ShortTermMemory()
        stm.add("新闻", "内容1")
        stm.add("公告", "内容2")
        text = stm.render()
        assert "## 新闻" in text and "## 公告" in text

    def test_clear_keeps_evicted_counter(self):
        stm = ShortTermMemory(budget=100)
        stm.add("a", "x" * 80)
        stm.add("b", "y" * 80)
        assert stm.evicted == 1
        stm.clear()
        assert stm.keys == [] and stm.evicted == 1  # 留痕保留


class TestLongTermMemory:
    @pytest.fixture
    def ltm(self, tmp_path):
        return LongTermMemory(str(tmp_path / "summaries.json"))

    def _summary(self, symbol, **kw):
        return CompanySummary(symbol=symbol, name=kw.get("name", ""),
                              conclusion=kw.get("conclusion", "结论"),
                              key_metrics=kw.get("metrics", {}),
                              updated_at=kw.get("updated_at", ""))

    def test_save_and_load_roundtrip(self, ltm):
        ltm.save_summary(self._summary("600519", name="贵州茅台",
                                       metrics={"roe": 32.1}))
        s = ltm.get_summary("600519")
        assert s.name == "贵州茅台"
        assert s.key_metrics == {"roe": 32.1}
        assert s.updated_at  # 自动填充时间戳

    def test_save_is_idempotent_per_symbol(self, ltm):
        ltm.save_summary(self._summary("600519", conclusion="旧结论"))
        ltm.save_summary(self._summary("600519", conclusion="新结论"))
        assert ltm.all_symbols() == ["600519"]
        assert ltm.get_summary("600519").conclusion == "新结论"

    def test_persisted_to_disk(self, ltm):
        ltm.save_summary(self._summary("600519"))
        assert os.path.exists(ltm.store_path)
        with open(ltm.store_path, encoding="utf-8") as f:
            assert "600519" in json.load(f)

    def test_peer_summaries_excludes_self_newest_first(self, ltm):
        ltm.save_summary(self._summary("600519", updated_at="2026-08-17T10:00:00"))
        ltm.save_summary(self._summary("000858", updated_at="2026-08-17T11:00:00"))
        ltm.save_summary(self._summary("600809", updated_at="2026-08-17T09:00:00"))
        peers = ltm.peer_summaries(exclude="600519")
        assert [p.symbol for p in peers] == ["000858", "600809"]

    def test_corrupted_file_rebuilt(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{broken", encoding="utf-8")
        ltm = LongTermMemory(str(p))
        assert ltm.all_symbols() == []  # 损坏文件重建为空，不抛异常

    def test_summary_text_limited(self):
        s = CompanySummary(symbol="600519", name="贵州茅台",
                           conclusion="长结论" * 200)
        assert len(s.as_text(max_chars=100)) <= 101


class TestHybridMemory:
    @pytest.fixture
    def mem(self, tmp_path):
        return HybridMemory({"memory_dir": str(tmp_path / "memory"),
                             "short_budget": 500})

    def test_ingest_table_compresses(self, mem):
        rows = [["期数", "营收"]] + [[f"Q{i}", i] for i in range(30)]  # 31 行
        mem.ingest("财报表格", rows, kind="table")
        assert "省略 21 行" in mem.short_term.render()

    def test_ingest_conclusion_persists_long_term(self, mem):
        mem.ingest("分析结论", "ROE 稳定在 30% 以上，盈利质量高",
                   kind="conclusion", symbol="600519")
        s = mem.long_term.get_summary("600519")
        assert s and "ROE" in s.conclusion

    def test_ingest_conclusion_requires_symbol(self, mem):
        with pytest.raises(ValueError):
            mem.ingest("结论", "x", kind="conclusion")

    def test_build_context_injects_peer_summaries(self, mem):
        mem.save_analysis(CompanySummary(
            symbol="000858", name="五粮液", sector="白酒",
            conclusion="估值偏低", key_metrics={"roe": 25.0}))
        mem.short_term.add("新闻", "茅台发布中报")
        ctx = mem.build_context("600519")
        assert "茅台发布中报" in ctx                    # 短期记忆
        assert "前序标的分析摘要" in ctx
        assert "[000858 五粮液]" in ctx and "估值偏低" in ctx
        assert "600519" not in ctx.split("前序标的")[1]  # 排除自身

    def test_reset_for_symbol_clears_short_only(self, mem):
        mem.short_term.add("k", "v")
        mem.save_analysis(CompanySummary(symbol="000858", conclusion="c"))
        mem.reset_for_symbol("600519")
        assert mem.short_term.keys == []
        assert mem.long_term.get_summary("000858") is not None

    def test_stats_tracks_eviction(self, mem):
        mem.ingest("a", "x" * 400, kind="fact")
        mem.ingest("b", "y" * 400, kind="fact")  # 触发逐出
        st = mem.stats()
        assert st["short_term_evicted"] == 1
        assert st["short_term_chars"] <= 500
        assert st["long_term_symbols"] == []

    def test_retrieve_knowledge_without_retriever(self, mem):
        assert mem.retrieve_knowledge("估值方法") == []
