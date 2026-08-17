# finance-report 数据产物说明

本目录存放 `finance-report` 技能（`jiuwenswarm/resources/agent/workspace/skills/finance-report/`）数据采集层的落盘产物。

## 目录结构

```
reports/finance-report/
├── data/
│   ├── {股票代码}_quote.json    # 行情数据（近一年日线，前复权）
│   ├── {股票代码}_filing.json   # 财务数据（最近 8 个报告期 + 近期公告）
│   └── {股票代码}_news.json     # 新闻 Deep Research 结果（含迭代轨迹 search_trace）
├── finance_kb/                  # 财务方法论知识库（RAGRetriever 外部记忆）
│   ├── docs/                    # 种子方法论文档（13 篇，可增删后重建）
│   └── index/index.json         # 分词+向量索引（可离线重建：build()）
├── charts/                      # CodeExecutor/ChartGenerator 生成的图表
└── memory/                      # 混合记忆长期层（HybridMemory）
    └── long_term/company_summaries.json  # 各标的分析结论摘要沉淀
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
json.dump(NewsCollector().collect("贵州茅台").to_dict(),
          open(f"reports/finance-report/data/{symbol}_news.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# 财务知识库：冷启动播种 + 检索
rag = RAGRetriever()
rag.ensure_kb()
print([h.to_dict() for h in rag.retrieve("白酒行业估值方法", top_k=3)])
PY
```

依赖：`akshare pandas openpyxl requests`（已在 `pyproject.toml` 声明）。

## 数据口径

| 字段 | 说明 |
| --- | --- |
| `source` | 实际命中的数据源。行情采集为三级降级链：akshare(东方财富) → 腾讯(前复权) → 新浪(不复权)；财报为 akshare 财务摘要；新闻为三级降级链：搜狗新闻搜索 → 新浪滚动财经(关键词过滤) → Bing(代理) |
| `collected_at` | 采集时刻（ISO 8601），满足赛题可溯源要求 |
| 财报 `total_assets`/`total_liabilities` | 由「股东权益 ÷ (1 − 资产负债率)」推导（财务摘要未直接披露） |
| 新闻 `search_trace` | Deep Research 每轮执行的查询与新增条目数（max_depth=3，信息饱和或达上限终止） |
| 衍生指标 | 毛利率/净利率/ROE/资产负债率均取披露口径，非自行计算 |

## 注意

- 行情降级链中新浪源为**不复权**数据，若 `source` 含「新浪」需注意与复权口径的差异
- 本机网络若屏蔽 `push2his.eastmoney.com`，akshare 会自动降级（属预期行为）
- 新闻 Deep Research 的查询精炼由 MiniMax-M2 完成（读项目根 `.env`）；LLM 不可用时自动降级规则法
- RAG 向量化使用智谱 embedding-3（2048 维，读 `.env` 的 `ZHIPU_API_KEY`；MiniMax Token Plan 订阅 Key 实测不支持 embedding 接口）；智谱不可用时自动降级本地字符 bigram TF-IDF（零依赖、可离线复现）
- 混合记忆分流（防批量分析记忆爆炸）：大表格只入短期且压缩为表头+前后 5 行；分析结论同步沉淀长期记忆 `memory/long_term/`，跨标的分析时以摘要形态注入后续上下文；方法论知识经 RAG 外部记忆按需检索
- 财务分析口径：财务指标取披露口径；同比与上年同期报告期对比（非上一期）；PE 按年化净利润（Q1×4/Q2×2/Q3×4/3），市值口径为「亿元 vs 净利润元」自动换算
- CodeExecutor 安全：AST 白名单（仅 pandas/numpy/matplotlib 等）+ 禁 exec/eval/compile 及 `__subclasses__` 等逃逸属性；中文字体 SimHei、Agg 后端预配置
- 单元测试见 `tests/unit_tests/finance/`（全部 mock，无网络依赖）
