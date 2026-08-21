# -*- coding: utf-8 -*-
"""finance-report e2e 公共夹具：一次 pipeline 运行，多处产物断言

实现依据：docs/plans/2026-08-20-finance-report-e2e-design.md §二（v2）。
对设计文档的两处修正（实现时发现的逻辑错误）：
- D1 确定性比对时序：设计稿在第二次 invest 后才把「当前」Portfolio 备份为
  portfolio_run1.json 再与自身比对——同字节恒真，失去校验意义。
  本实现在 reproduce（第一次决策）完成后立即备份，二次决策后比对两份产物。
- D2 run_stats phases：latest run 是第二次独立 invest（phase 少于 4），
  断言改为「近 5 次 run 中存在 phases ≥ 4 的完整链路运行」（见测试侧）。
"""

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
    """subprocess 调 run_report.py 一个阶段；失败抛 AssertionError（strict fail）

    encoding 显式 utf-8：子进程 PYTHONUTF8=1 输出 UTF-8，Windows 父进程
    text=True 缺省按 GBK 解码会 UnicodeDecodeError。
    """
    cmd = [sys.executable, "-X", "utf8", str(RUN_REPORT_PY)] + args
    proc = subprocess.run(
        cmd, cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout)
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
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1800)
    assert proc.returncode == 0, (
        f"reproduce.py 失败\nSTDOUT: {proc.stdout[-1000:]}\n"
        f"STDERR: {proc.stderr[-1000:]}")

    # D1 修正：第一次决策产物立即备份，供 TestDecision 确定性比对
    snapshot = OUTPUT_DIR / "decision_log" / "portfolio_run1.json"
    snapshot.write_bytes((OUTPUT_DIR / "Portfolio.json").read_bytes())

    # 2. 三类研报（C1 修复：industry/macro 不在 reproduce.py 内，e2e 自行生成）
    run_stage(["company", "--target", "600519", "--name", "贵州茅台", "--save"],
              env=e2e_env, timeout=600)
    run_stage(["industry", "--name", "消费板块", "--save"],
              env=e2e_env, timeout=600)
    run_stage(["macro", "--period", "2026Q2", "--save"],
              env=e2e_env, timeout=600)

    # 3. 二次决策 → 确定性比对（M5：同评分缓存两次 invest 须字节级一致）
    run_stage(["invest", "--pool-file", str(POOL_FILE),
               "--use-cached-scores", "--max-positions", "8",
               "--skip-reports", "--save"],
              env=e2e_env, timeout=300)
    return {"proc": proc}
