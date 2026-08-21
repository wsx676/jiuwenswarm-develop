# -*- coding: utf-8 -*-
"""原子写单元测试（优化方案 11）

覆盖：产物与直接 json.dump 逐字节一致 / 无 .tmp 残留 / 写入失败
不损坏既有文件（旧内容保留 + 临时文件清理）/ 目录自动创建 /
telemetry.save 与 investor._save 走原子写后仍可读。
"""

import json
import os

import pytest

from common.file_io import atomic_write_json


class TestAtomicWrite:
    def test_byte_identical_to_direct_dump(self, tmp_path):
        obj = {"组合": {"600519": 0.4}, "说明": "中文内容", "n": 1}
        path = str(tmp_path / "decision.json")
        atomic_write_json(path, obj)
        with open(path, encoding="utf-8") as f:
            assert f.read() == json.dumps(
                obj, ensure_ascii=False, indent=2)

    def test_no_tmp_residue(self, tmp_path):
        path = str(tmp_path / "scores_cache.json")
        atomic_write_json(path, {"scores": {}})
        assert not os.path.exists(path + ".tmp")

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "run_stats.json")
        atomic_write_json(path, {"runs": []})
        assert os.path.exists(path)

    def test_failure_keeps_old_content(self, tmp_path, monkeypatch):
        """写入中断：目标文件保留旧内容，.tmp 清理（防半截 JSON）"""
        path = str(tmp_path / "decision.json")
        atomic_write_json(path, {"version": 1})

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(json, "dump", boom)
        with pytest.raises(OSError):
            atomic_write_json(path, {"version": 2})
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == {"version": 1}   # 旧内容完好
        assert not os.path.exists(path + ".tmp")     # 临时文件已清理

    def test_indent_none_compact(self, tmp_path):
        path = str(tmp_path / "compact.json")
        atomic_write_json(path, {"a": 1}, indent=None)
        with open(path, encoding="utf-8") as f:
            assert f.read() == json.dumps({"a": 1}, ensure_ascii=False)


class TestIntegrationSites:
    def test_run_stats_save_atomic_readable(self, tmp_path):
        from common.telemetry import RunStats
        stats = RunStats()
        stats.add_llm_failover()
        path = stats.save(str(tmp_path))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["runs"][-1]["llm_failover"] == 1
        assert not os.path.exists(path + ".tmp")

    def test_investor_save_atomic_readable(self, tmp_path):
        from agents.investor import InvestorAgent
        inv = InvestorAgent({})
        inv._save({"600519": 0.4}, str(tmp_path), {"600519": 80.0})
        for name in ("Portfolio.json",
                     os.path.join("decision_log", "decision.json")):
            path = tmp_path / name
            assert path.exists()
            with open(path, encoding="utf-8") as f:
                json.load(f)                      # 合法 JSON 可读
            assert not os.path.exists(str(path) + ".tmp")
