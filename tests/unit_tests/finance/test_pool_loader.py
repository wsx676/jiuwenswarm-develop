# -*- coding: utf-8 -*-
"""pool_loader 单元测试：公司池解析、板块分组、白名单硬校验"""

import pytest

from collectors.pool_loader import (
    find_sector,
    load_pool,
    sector_peers,
    validate_symbol,
    whitelist_symbols,
)
from conftest import POOL_FILE


@pytest.fixture
def mini_pool_file(tmp_path):
    """构造最小公司池 xlsx：两列板块，单元格形如 '600519 贵州茅台'"""
    import pandas as pd

    df = pd.DataFrame({
        "消费板块": ["600519 贵州茅台", "000858 五粮液", None, "-"],
        "金融板块": ["601288 农业银行", "600036 招商银行", None, None],
    })
    path = tmp_path / "pool.xlsx"
    df.to_excel(path, index=False)
    return str(path)


class TestLoadPool:
    def test_parse_sectors_and_symbols(self, mini_pool_file):
        pool = load_pool(mini_pool_file)
        assert set(pool) == {"消费板块", "金融板块"}
        assert pool["消费板块"] == [("600519", "贵州茅台"), ("000858", "五粮液")]
        assert pool["金融板块"] == [("601288", "农业银行"), ("600036", "招商银行")]

    def test_skips_blank_and_dash_cells(self, mini_pool_file):
        pool = load_pool(mini_pool_file)
        assert len(pool["消费板块"]) == 2  # None 与 "-" 均剔除


class TestWhitelist:
    def test_symbol_in_pool_accepted(self, mini_pool_file):
        pool = load_pool(mini_pool_file)
        assert validate_symbol(pool, "600519") is True

    def test_symbol_outside_pool_rejected(self, mini_pool_file):
        """白名单硬校验：列表外代码一律拒绝（标的越界防护）"""
        pool = load_pool(mini_pool_file)
        assert validate_symbol(pool, "999999") is False
        assert validate_symbol(pool, "") is False

    def test_whitelist_symbols_union(self, mini_pool_file):
        pool = load_pool(mini_pool_file)
        assert whitelist_symbols(pool) == {
            "600519", "000858", "601288", "600036",
        }


class TestSectorPeers:
    def test_find_sector(self, mini_pool_file):
        pool = load_pool(mini_pool_file)
        assert find_sector(pool, "600519") == "消费板块"
        assert find_sector(pool, "999999") is None

    def test_peers_exclude_self(self, mini_pool_file):
        """同板块天然竞对：取板块内其他标的，不含自身"""
        pool = load_pool(mini_pool_file)
        assert sector_peers(pool, "600519") == [("000858", "五粮液")]
        assert sector_peers(pool, "999999") == []


class TestRealPool:
    """真实公司池（example/上市公司列表.xlsx）验收标准测试"""

    @pytest.mark.skipif(not POOL_FILE.exists(), reason="公司池 xlsx 不存在")
    def test_real_pool_49_symbols_6_sectors(self):
        pool = load_pool(str(POOL_FILE))
        assert len(pool) == 6
        assert sum(len(items) for items in pool.values()) == 49

    @pytest.mark.skipif(not POOL_FILE.exists(), reason="公司池 xlsx 不存在")
    def test_real_pool_benchmark_symbol(self):
        pool = load_pool(str(POOL_FILE))
        assert validate_symbol(pool, "600519")
        assert find_sector(pool, "600519") == "消费板块"
