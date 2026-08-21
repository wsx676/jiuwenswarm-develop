# -*- coding: utf-8 -*-
"""fail-loud 启动配置校验单元测试（优化方案 9）

覆盖：合法配置通过 / score_threshold 值域 / max_weight 值域 /
正整数约束 / pool_file 存在性 / 布尔开关类型 / 多错误聚合输出。
"""

import pytest

from common.config_check import ConfigError, validate_startup_config


class TestValid:
    def test_empty_config_passes(self):
        validate_startup_config({})
        validate_startup_config(None)

    def test_legal_investor_config_passes(self):
        validate_startup_config({"investor": {
            "score_threshold": 60.0,
            "max_weight_per_stock": 0.4,
            "min_position_count": 3,
            "max_positions": 8,
        }})

    def test_legal_switches_pass(self):
        validate_startup_config({
            "news_filter": {"enabled": True, "llm_grade_enabled": False},
            "material_rescue": {"enabled": True},
            "query_variants": True,
        })


class TestScoreThreshold:
    @pytest.mark.parametrize("bad", ["60", None, -1, 120, True])
    def test_invalid(self, bad):
        with pytest.raises(ConfigError, match="score_threshold"):
            validate_startup_config(
                {"investor": {"score_threshold": bad}})

    def test_boundary_ok(self):
        validate_startup_config({"investor": {"score_threshold": 0}})
        validate_startup_config({"investor": {"score_threshold": 100}})


class TestMaxWeight:
    @pytest.mark.parametrize("bad", [0, 1.2, "0.4", True])
    def test_invalid(self, bad):
        with pytest.raises(ConfigError, match="max_weight_per_stock"):
            validate_startup_config(
                {"investor": {"max_weight_per_stock": bad}})

    def test_one_is_legal(self):
        validate_startup_config(
            {"investor": {"max_weight_per_stock": 1.0}})


class TestIntegerFields:
    @pytest.mark.parametrize("key", ["min_position_count", "max_positions"])
    @pytest.mark.parametrize("bad", [0, -1, 2.5, "3", True])
    def test_invalid(self, key, bad):
        with pytest.raises(ConfigError, match=key):
            validate_startup_config({"investor": {key: bad}})


class TestPoolFile:
    def test_missing_pool_file_fails(self):
        with pytest.raises(ConfigError, match="pool_file"):
            validate_startup_config(
                {"pool_file": "/nonexistent/pool.xlsx"})

    def test_existing_pool_file_passes(self, tmp_path):
        pool = tmp_path / "pool.xlsx"
        pool.write_bytes(b"x")
        validate_startup_config({"pool_file": str(pool)})


class TestSwitchTypes:
    @pytest.mark.parametrize("section,key", [
        ("news_filter", "enabled"),
        ("news_filter", "llm_grade_enabled"),
        ("material_rescue", "enabled"),
    ])
    def test_non_bool_flag_fails(self, section, key):
        with pytest.raises(ConfigError, match=key):
            validate_startup_config({section: {key: "yes"}})

    def test_query_variants_non_bool_fails(self):
        with pytest.raises(ConfigError, match="query_variants"):
            validate_startup_config({"query_variants": 1})


class TestErrorAggregation:
    def test_multiple_errors_all_listed(self):
        with pytest.raises(ConfigError) as exc_info:
            validate_startup_config({
                "investor": {"score_threshold": "六十",
                             "max_positions": 0},
                "pool_file": "/nonexistent/pool.xlsx",
            })
        msg = str(exc_info.value)
        assert "score_threshold" in msg
        assert "max_positions" in msg
        assert "pool_file" in msg

    def test_config_error_is_value_error(self):
        # ConfigError 继承 ValueError：调用方可按既有异常链路兜底
        assert issubclass(ConfigError, ValueError)
