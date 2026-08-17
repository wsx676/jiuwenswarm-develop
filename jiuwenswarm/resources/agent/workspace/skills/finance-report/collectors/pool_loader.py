# -*- coding: utf-8 -*-
"""公司池加载与白名单校验

解析组委会公布的上市公司列表（example/上市公司列表.xlsx，六大板块分类），
作为选股白名单与板块竞对分组依据：
- 白名单硬校验：列表外代码一律剔除（标的越界防护）
- 同板块公司天然为竞对：行业分析时直接提取板块内竞对，无需模型猜测

xlsx 格式：每列一个板块（表头为板块名），单元格形如 "600519 贵州茅台"。
"""

from typing import Dict, List, Optional


def load_pool(pool_file: str) -> Dict[str, List[tuple]]:
    """加载公司池，返回 {板块名: [(代码, 简称), ...]}

    Args:
        pool_file: 上市公司列表 xlsx 路径
    """
    import pandas as pd

    df = pd.read_excel(pool_file, dtype=str)
    pool: Dict[str, List[tuple]] = {}
    for sector in df.columns:
        items = []
        for cell in df[sector].dropna():
            cell = str(cell).strip()
            if not cell or cell == "-":
                continue
            parts = cell.split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                items.append((parts[0], parts[1]))
        if items:
            pool[str(sector).strip()] = items
    return pool


def whitelist_symbols(pool: Dict[str, List[tuple]]) -> set:
    """提取全部合法股票代码集合（选股白名单）"""
    return {symbol for items in pool.values() for symbol, _ in items}


def find_sector(pool: Dict[str, List[tuple]], symbol: str) -> Optional[str]:
    """查询标的所属板块；不在池内返回 None"""
    for sector, items in pool.items():
        if any(s == symbol for s, _ in items):
            return sector
    return None


def sector_peers(
    pool: Dict[str, List[tuple]], symbol: str
) -> List[tuple]:
    """获取同板块竞对名单（不含自身）——板块内公司天然为竞对"""
    sector = find_sector(pool, symbol)
    if sector is None:
        return []
    return [(s, n) for s, n in pool[sector] if s != symbol]


def validate_symbol(pool: Dict[str, List[tuple]], symbol: str) -> bool:
    """白名单硬校验：标的必须在组委会公布的列表内"""
    return symbol in whitelist_symbols(pool)
