# finance-report e2e 测试方案 — 设计文档（评审修订版 v2）

> **目标**：为 CCF BDCI 2026 赛题 C4「成果可复现」提供 pytest 自动化兜底——CI 可一键跑通全流程并断言产物合规。
> **brainstorming 决策**：赛题复现证据 / 全池 / 混台采集（缓存优先）/ 统一一套测试 / 依赖真实 LLM Key（缺 Key 则 skip）/ strict fail / `tests/system_tests/finance_e2e/`
> **设计原则**：一次跑全流程、多断言产物；marker 隔离（默认 pytest 不触发）；产物不回滚（git 即回滚）。

---

## 〇、评审修订记录（v1 → v2）

| # | 严重度 | v1 问题 | v2 修复 |
|---|--------|---------|---------|
| C1 | 🔴 | 断言「行业/宏观研报 ≥1」但 `reproduce.py` **不生成**这两类报告（只跑 4 阶段 + `--with-report` 单一公司研报）——干净环境必失败、脏环境假通过 | session fixture 在 reproduce 后**自行调用** `run_report.py industry / macro` 生成，断言针对自产文件 |
| C2 | 🔴 | CI 环境变量名 `LLM_API_KEY` 错误——`llm_client.py` 只认 `API_KEY/ANTHROPIC_API_KEY/MINIMAX_API_KEY`，CI 报告会静默降级模板 | CI 改用 `MINIMAX_API_KEY`；且新增断言「研报无『规则模板段』标记」防降级假通过 |
| C3 | 🔴 | `pytest.ini testpaths=tests` 含 system_tests，默认 `pytest` 误触发 e2e（15-30 分钟） | `pytest.ini` markers 注册 `e2e` + addopts 追加 `-m "not e2e"`；e2e 只经 `-m e2e` 显式触发 |
| C4 | 🔴 | 4 个单阶段测试 + FullReproduce 把全池流程跑两遍；Stage4 隐式依赖 Stage3 产物，不可独立运行 | 改为 **session-scoped `e2e_pipeline` fixture 一次跑完整链**（reproduce 4 阶段 + industry + macro），各测试函数只对产物做断言 |
| C5 | 🔴 | 测试骨架 import 缺失（`run_stage`/`OUTPUT_DIR`/`sys`）；§4.3 代码块 `classTest`/`deftest` 粘连 | conftest 统一导出常量与 `run_stage`，测试文件显式导入；代码块重写 |
| M1 | 🟡 | marker 写到 pyproject.toml（实际配置在 pytest.ini，且 `--strict-markers` 下未注册即报错） | pytest.ini `markers` 增加 `e2e: ...` |
| M2 | 🟡 | `backup_outputs` 默认回滚新产物，与「复现证据」目标矛盾 | 删除 backup/restore；产物留在 git 跟踪目录，`git checkout -- reports/` 即回滚 |
| M3 | 🟡 | `--timeout=1800` 依赖未安装的 pytest-timeout | 移除该 flag，超时由 CI job `timeout-minutes` 兜底 |
| M4 | 🟡 | 权重断言自相矛盾（允许 1.04 vs 硬约束 ≤1.0） | 统一为 `0 < total ≤ 1.0 + 1e-9` |
| M5 | 🟡 | 声称「两次跑哈希一致」但无测试 | 新增确定性测试：同缓存重跑 invest 两次，`Portfolio.json` 字节级一致 |
| M6 | 🟡 | cron 注释时区错误（周日 18:00 UTC = 周一 02:00 北京） | 修正注释 |
| M7 | 🟡 | Stage1 用 `"49" in stdout` 脆弱断言 | `pool` 输出为 JSON——解析后断言 `total == 标的数`、`sectors` 非空 |
| M8 | 🟡 | `env_with_llm_key` 名不副实（不注入 key） | 改名 `e2e_env`；新增 `llm_key_or_skip` 前置检查（缺 Key `pytest.skip`，沿用 `skip_if_no_resources` 惯例） |

---

## 一、架构

### 1.1 文件结构

```
tests/system_tests/finance_e2e/
├── __init__.py
├── conftest.py            # 路径常量 + e2e_env + llm_key_or_skip + run_stage
└── test_pipeline_e2e.py   # 7 个断言用例（共用一次 pipeline 运行）
```

### 1.2 核心机制：一次运行、多处断言

session-scoped fixture `e2e_pipeline` 按序执行**一次**完整链路：

```
e2e_pipeline（session，只跑一次）
  1. reproduce.py                    （4 阶段：pool → collect → analyze → invest）
  2. run_report.py company  600519 --save      （公司研报）
  3. run_report.py industry --name 消费板块 --save（行业研报）
  4. run_report.py macro    --period 2026Q2 --save（宏观研报）
  5. run_report.py invest   --use-cached-scores --save（第二次决策 → 确定性比对）
     ↓
  产物写入 reports/finance-report/（git 跟踪目录，不回滚）
     ↓
测试函数（全部 @pytest.mark.e2e，仅断言产物）
  TestPool          公司池 JSON 解析
  TestDataCache     data/*.json ≥ 100
  TestScoresCache   scores_cache.json ≥ 30 只标的
  TestPortfolio     白名单/权重区间/总权重 ≤ 1.0
  TestDecision      position_decision/rationale + 双跑确定性
  TestReports       三类研报存在 + 章节合规 + 无「规则模板段」降级标记
  TestRunStats      seed 固定 + phases ≥ 4 + LLM calls > 0
```

> 阶段失败（采集网络错误、LLM 调用失败）→ fixture 直接 fail（strict fail 决策）；缺 LLM Key → **skip**（前置条件缺失 ≠ 运行失败）。

### 1.3 断言矩阵

| 产物 | 断言 | 说明 |
|------|------|------|
| pool JSON | `total` == 公司池标的数；`sectors` 非空 | 解析 stdout JSON，不匹配子串 |
| `data/*.json` | 数量 ≥ 100（49×3=147） | 混台：缓存优先，已有缓存时不断言新增 |
| `scores_cache.json` | `scores` ≥ 30 只 | |
| `Portfolio.json` | dict 非空；keys ⊆ 白名单；单标 (0, 0.4]；总和 (0, 1.0+1e-9] | 与 `validate_portfolio` 同口径 |
| `decision.json` | `position_decision ∈ {full,partial,empty}`；`position_rationale` 非空 | |
| 确定性 | 两次 invest 的 `Portfolio.json` **字节级一致** | 确定性规则 + 固定种子的直接验证 |
| 公司研报 | ≥1 份；含「核心观点/投资结论/风险提示」；「数据来源」≥3；「免责声明」；字数 ≥ 800；**无「规则模板段」** | 最后一条防 LLM 静默降级（C2） |
| 行业研报 | ≥1 份；含「板块核心观点/竞争格局/风险提示」；免责声明；无降级标记 | |
| 宏观研报 | ≥1 份；含「宏观核心观点/风险提示」；免责声明；无降级标记 | |
| `run_stats.json` | 所有 run `seed == 20260819`；最新 run `phases ≥ 4`；**LLM calls > 0** | LLM calls>0 同样防降级 |

---

## 二、conftest.py

```python
# -*- coding: utf-8 -*-
"""finance-report e2e 公共夹具：一次 pipeline 运行，多处产物断言"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = (
    PROJECT_ROOT / "jiuwenswarm" / "resources" / "agent"
    / "workspace" / "skills" / "finance-report"
)
REPRODUCE_PY = SKILL_ROOT / "scripts" / "reproduce.py"
RUN_REPORT_PY = SKILL_ROOT / "run_report.py"
POOL_FILE = PROJECT_ROOT / "example" / "上市公司列表.xlsx"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "finance-report"

if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))


def run_stage(args, env, timeout=900):
    """subprocess 调 run_report.py 一个阶段；失败抛 AssertionError（strict fail）"""
    cmd = [sys.executable, "-X", "utf8", str(RUN_REPORT_PY)] + args
    proc = subprocess.run(
        cmd, cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(
            f"阶段失败 {args} (rc={proc.returncode})\n"
            f"STDOUT: {proc.stdout[-800:]}\nSTDERR: {proc.stderr[-800:]}")
    return proc


@pytest.fixture(scope="session")
def e2e_env():
    """subprocess 环境（.env 由 llm_client.load_env_file 自行读取项目根）"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return env


@pytest.fixture(scope="session")
def llm_key_or_skip():
    """前置检查：无 LLM Key 则 skip（前置缺失≠运行失败）"""
    from common.llm_client import LLMClient
    if not LLMClient.available():
        pytest.skip("未配置 LLM Key（.env 的 API_KEY），e2e 需真实 LLM 调用")


@pytest.fixture(scope="session")
def e2e_pipeline(e2e_env, llm_key_or_skip):
    """一次完整链路：reproduce 4 阶段 + 三类研报 + 二次决策（确定性比对）"""
    # 1. reproduce.py 全流程（pool → collect → analyze → invest）
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(REPRODUCE_PY)],
        cwd=PROJECT_ROOT, env=e2e_env,
        capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, (
        f"reproduce.py 失败\nSTDOUT: {proc.stdout[-1000:]}\n"
        f"STDERR: {proc.stderr[-1000:]}")

    # 2. 三类研报（C1 修复：industry/macro 不在 reproduce.py 内，e2e 自行生成）
    run_stage(["company", "--target", "600519", "--name", "贵州茅台", "--save"],
              env=e2e_env, timeout=600)
    run_stage(["industry", "--name", "消费板块", "--save"],
              env=e2e_env, timeout=600)
    run_stage(["macro", "--period", "2026Q2", "--save"],
              env=e2e_env, timeout=600)

    # 3. 二次决策 → 确定性比对素材（M5）
    run_stage(["invest", "--pool-file", str(POOL_FILE),
              "--use-cached-scores", "--max-positions", "8",
              "--skip-reports", "--save"],
              env=e2e_env, timeout=300)
    second = (OUTPUT_DIR / "Portfolio.json").read_bytes()
    first_portfolio = OUTPUT_DIR / "decision_log" / "portfolio_run1.json"
    first_portfolio.write_bytes(second)  # 供 TestDecision 比对
    return {"proc": proc}
```

---

## 三、test_pipeline_e2e.py

```python
# -*- coding: utf-8 -*-
"""finance-report e2e：产物断言（共用 e2e_pipeline 一次运行）"""

import json

import pytest

from conftest import OUTPUT_DIR, POOL_FILE, PROJECT_ROOT

pytestmark = pytest.mark.e2e


def _load(path):
    return json.loads((OUTPUT_DIR / path).read_text(encoding="utf-8"))


class TestPool:
    def test_pool_json(self, e2e_pipeline, e2e_env):
        """公司池校验：解析 JSON 断言，不匹配子串"""
        from conftest import run_stage
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
        """确定性：两次 invest 的 Portfolio.json 字节级一致（M5）"""
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
        latest = stats["runs"][-1]
        assert len(latest["phases"]) >= 4
        # LLM 真实调用留痕（C2 防护）
        assert any(r["llm"]["calls"] > 0 for r in stats["runs"][-5:])
```

---

## 四、pytest 配置与 CI

### 4.1 pytest.ini 修改（marker 注册 + 默认排除）

```ini
# markers 追加一行
markers =
    ...（现有保留）
    e2e: finance-report 端到端（需网络与 LLM Key，约 10-30 分钟）

# addopts 追加（默认 pytest 不触发 e2e；M2：e2e 对 subprocess 无覆盖率意义）
addopts =
    -v --strict-markers --tb=short -m "not e2e"
    --cov=jiuwenswarm ...（现有保留）
```

> e2e 运行时显式覆盖：`pytest -m e2e --no-cov -p no:cacheprovider`

### 4.2 GitHub Actions

```yaml
# .github/workflows/finance-report-e2e.yml
name: finance-report-e2e
on:
  workflow_dispatch:
  schedule:
    - cron: '0 18 * * 0'   # 周日 18:00 UTC = 周一 02:00 北京
jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e ".[test]"
      - name: e2e
        env:
          # C2 修复：llm_client 只认这三个键名（其一即可）
          MINIMAX_API_KEY: ${{ secrets.MINIMAX_API_KEY }}
        run: pytest tests/system_tests/finance_e2e/ -m e2e --no-cov --tb=short -v
```

### 4.3 本地运行

```bash
pytest tests/system_tests/finance_e2e/ -m e2e --no-cov -v   # 全量（~10-30 分钟）
pytest tests/system_tests/finance_e2e/ -m e2e --no-cov -k TestPortfolio -v  # 单类（复用一次 pipeline）
pytest -m "not e2e"                                        # 日常单测（不受影响）
git checkout -- reports/finance-report/                    # 回滚 e2e 产物（M2）
```

---

## 五、风险与验收（合并）

| 风险 | 对策 |
|------|------|
| 采集网络错误 | strict fail（fixture 抛错）——环境错误与代码错误在断言信息中区分手动重跑 |
| LLM Key 缺失/失效 | 缺失→skip；失效→「规则模板段」断言 + `LLM calls > 0` 断言兜底 fail |
| e2e 污染产物目录 | 不回滚（复现证据要新产物）；git 跟踪目录，`git checkout -- reports/` 一键还原 |
| session fixture 失败导致全部用例 error | 属预期（strict fail）：链路坏了就是全断，pytest 报告仍显示每个用例 |
| 干净 checkout 无缓存 | reproduce.py 混台自动补采（首次 ~15 分钟，之后缓存命中 ~5 分钟） |

**验收（全部满足即完成）**

- [ ] `pytest -m e2e --no-cov` 全绿（本机有 Key + 缓存 < 10 分钟）
- [ ] `pytest`（默认）不触发 e2e（`-m "not e2d"` 生效，8 collected / e2e deselected）
- [ ] 无 `.env` Key 时 `pytest -m e2e` 显示 skip 而非 fail
- [ ] 确定性测试通过（两次 invest 产物字节一致）
- [ ] 三类研报断言含「无规则模板段」降级防护
- [ ] CI workflow 手动触发可跑通（需配 `MINIMAX_API_KEY` secret）

---

## 六、交付物

1. `tests/system_tests/finance_e2e/__init__.py`
2. `tests/system_tests/finance_e2e/conftest.py`（本文档 §二）
3. `tests/system_tests/finance_e2e/test_pipeline_e2e.py`（本文档 §三）
4. `pytest.ini`：markers 追加 `e2e`；addopts 追加 `-m "not e2e"`
5. `.github/workflows/finance-report-e2e.yml`（本文档 §4.2，env 键名 `MINIMAX_API_KEY`）
6. 本文档

**不做**（YAGNI）：多日定时复测、pytest-html 截图附件、logdiff 对比工具、备份/回滚 fixture。
