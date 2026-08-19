# -*- coding: utf-8 -*-
"""一键端到端复现脚本（赛题要求：第三方可完整复现投资决策过程与资源消耗）

用法（项目根目录执行）：
    python jiuwenswarm/resources/agent/workspace/skills/finance-report/scripts/reproduce.py
    # 可选参数：
    #   --sector 消费板块      仅复现单板块（更快）
    #   --with-report          末尾追加生成一份示例个股研报（耗时较长）
    #   --pool-file PATH       自定义公司池 xlsx（缺省 example/上市公司列表.xlsx）

复现流程（确定性顺序，采集缓存优先、幂等可重跑）：
    1. pool     公司池校验（枚举板块/标的）
    2. collect  全池采集：行情/财报/新闻（缓存优先，缺什么采什么）
    3. analyze  分析引擎 + 因子打分（落盘 scores_cache.json）
    4. invest   选股 + 仓位配置（输出 Portfolio.json 与 decision.json，
                决策日志含 position_decision/position_rationale 阐明仓位逻辑）

资源消耗数据：reports/finance-report/decision_log/run_stats.json
（各阶段耗时 / LLM Token 累计 / 固定种子 / 失败留痕，遥测自动追加）。
"""

import argparse
import json
import os
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.dirname(_SCRIPT_DIR)
# finance-report → skills → workspace → agent → resources → jiuwenswarm → 项目根
_PROJECT_ROOT = os.path.abspath(os.path.join(_SKILL_ROOT, *[".."] * 6))
RUN_REPORT = os.path.join(_SKILL_ROOT, "run_report.py")
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "reports", "finance-report")


def _run(title: str, args: list) -> None:
    """执行 run_report.py 一个阶段；失败即终止（复现完整性优先）"""
    print(f"\n=== {title} ===", flush=True)
    cmd = [sys.executable, "-X", "utf8", RUN_REPORT] + args
    env = dict(os.environ, PYTHONUTF8="1")
    proc = subprocess.run(cmd, cwd=_PROJECT_ROOT, env=env)
    if proc.returncode != 0:
        print(f"[复现失败] {title} 退出码 {proc.returncode}", file=sys.stderr)
        sys.exit(proc.returncode)


def _summary() -> None:
    """复现产物清单与关键决策摘要"""
    print("\n=== 复现产物 ===", flush=True)
    portfolio_path = os.path.join(OUTPUT_DIR, "Portfolio.json")
    decision_path = os.path.join(OUTPUT_DIR, "decision_log", "decision.json")
    stats_path = os.path.join(OUTPUT_DIR, "decision_log", "run_stats.json")

    if os.path.exists(portfolio_path):
        with open(portfolio_path, encoding="utf-8") as f:
            portfolio = json.load(f)
        print(f"Portfolio.json: {len(portfolio)} 只标的，"
              f"总权重 {sum(portfolio.values()):.2f}")
    if os.path.exists(decision_path):
        with open(decision_path, encoding="utf-8") as f:
            log = json.load(f)
        print(f"decision.json: position_decision={log.get('position_decision')}")
        print(f"仓位理由: {log.get('position_rationale', '')}")
    if os.path.exists(stats_path):
        print(f"资源消耗（阶段耗时/Token/种子）: {stats_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="finance-report 一键端到端复现（投资决策过程可复现）")
    parser.add_argument(
        "--pool-file",
        default=os.path.join("example", "上市公司列表.xlsx"),
        help="公司池列表 xlsx 路径（相对项目根）")
    parser.add_argument(
        "--sector", default="",
        help="仅复现单板块（如 消费板块，更快）；缺省全池")
    parser.add_argument(
        "--with-report", action="store_true",
        help="末尾追加生成一份示例个股研报（贵州茅台，耗时较长）")
    args = parser.parse_args()

    print(f"项目根目录: {_PROJECT_ROOT}")
    print(f"公司池: {args.pool_file}")
    sector_args = ["--sector", args.sector] if args.sector else []

    _run("阶段 1/4：公司池校验", ["pool"])
    _run("阶段 2/4：全池采集（缓存优先，断点续采）",
         ["research", "--stage", "collect",
          "--pool-file", args.pool_file] + sector_args)
    _run("阶段 3/4：分析引擎 + 因子打分（落盘评分缓存）",
         ["research", "--stage", "analyze",
          "--pool-file", args.pool_file, "--save"] + sector_args)
    _run("阶段 4/4：投资决策（Portfolio.json + 决策日志）",
         ["invest", "--pool-file", args.pool_file,
          "--use-cached-scores", "--max-positions", "8",
          "--skip-reports", "--save"] + sector_args)
    if args.with_report:
        _run("示例个股研报（贵州茅台）",
             ["company", "--target", "600519", "--name", "贵州茅台",
              "--save"])

    _summary()
    print("\n复现完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
