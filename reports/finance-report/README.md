# finance-report 数据产物说明

本目录存放 `finance-report` 技能（`jiuwenswarm/resources/agent/workspace/skills/finance-report/`）数据采集层的落盘产物。

## 目录结构

```
reports/finance-report/data/
├── {股票代码}_quote.json    # 行情数据（近一年日线，前复权）
└── {股票代码}_filing.json   # 财务数据（最近 8 个报告期 + 近期公告）
```

## 如何复现这批数据

```bash
python - <<'PY'
import json, sys
sys.path.insert(0, r"jiuwenswarm/resources/agent/workspace/skills/finance-report")
from collectors.quote_collector import QuoteCollector
from collectors.filing_collector import FilingCollector

symbol, name = "600519", "贵州茅台"
json.dump(QuoteCollector().collect(symbol, name).to_dict(),
          open(f"reports/finance-report/data/{symbol}_quote.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
json.dump(FilingCollector().collect(symbol).to_dict(),
          open(f"reports/finance-report/data/{symbol}_filing.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
PY
```

依赖：`akshare pandas openpyxl requests`（已在 `pyproject.toml` 声明）。

## 数据口径

| 字段 | 说明 |
| --- | --- |
| `source` | 实际命中的数据源。行情采集为三级降级链：akshare(东方财富) → 腾讯(前复权) → 新浪(不复权)；财报为 akshare 财务摘要 |
| `collected_at` | 采集时刻（ISO 8601），满足赛题可溯源要求 |
| 财报 `total_assets`/`total_liabilities` | 由「股东权益 ÷ (1 − 资产负债率)」推导（财务摘要未直接披露） |
| 衍生指标 | 毛利率/净利率/ROE/资产负债率均取披露口径，非自行计算 |

## 注意

- 行情降级链中新浪源为**不复权**数据，若 `source` 含「新浪」需注意与复权口径的差异
- 本机网络若屏蔽 `push2his.eastmoney.com`，akshare 会自动降级（属预期行为）
- 单元测试见 `tests/unit_tests/finance/`（全部 mock，无网络依赖）
