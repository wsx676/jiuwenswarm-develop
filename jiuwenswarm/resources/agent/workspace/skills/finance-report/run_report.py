# -*- coding: utf-8 -*-
"""命令行入口脚本

支持六类任务：
- company  : 生成公司研报（输出 {股票代码}.md）
- industry : 生成行业研报
- macro    : 生成宏观研报
- invest   : 投资决策（公司池选股 + 仓位配置，输出 Portfolio.json）
- pool     : 校验公司池并枚举板块/标的清单（Swarmflow 选股阶段）
- research : 公司池采集/分析两阶段（Swarmflow 采集/分析阶段）

用法示例：
    python run_report.py company --target 600519 --name 贵州茅台 --save
    python run_report.py industry --name 半导体 --save
    python run_report.py macro --period 2026Q2 --save
    python run_report.py invest --pool-file example/上市公司列表.xlsx --save
    python run_report.py invest --pool-file example/上市公司列表.xlsx --sector 消费 --save
    python run_report.py pool
    python run_report.py research --stage collect
    python run_report.py research --stage analyze --save
"""

import argparse
import json
import os
import sys

from orchestrator import ReportOrchestrator, ReportRequest
from agents.planner import DEFAULT_POOL_FILE
from common.telemetry import RUN_STATS

# 默认输出目录（提交格式对齐：个股投资研报/股票代码.md + Portfolio.json）
DEFAULT_OUTPUT_DIR = os.path.join("reports", "finance-report")

# 项目根（与 researcher.DEFAULT_DATA_DIR / planner.DEFAULT_POOL_FILE 同口径：
# 技能目录向上 6 层），保证任何 cwd 下产物都落项目根 reports/
_SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SKILL_ROOT, *[".."] * 6))


def resolve_output_dir(path) -> str:
    """输出目录解析：显式 --output-dir 优先；缺省锚定项目根
    （技能根目录执行时 reports/ 相对路径会误落技能内，与 data/
    缓存目录口径不一致）
    """
    if path:
        return path
    return os.path.join(_PROJECT_ROOT, *DEFAULT_OUTPUT_DIR.split(os.sep))


def resolve_pool_file(path: str) -> str:
    """公司池路径解析：显式路径存在则用之；否则回退项目根默认池
    （技能根目录执行时 example/ 相对路径不存在，避免 FileNotFoundError）
    """
    if path and os.path.exists(path):
        return path
    if path and path != DEFAULT_POOL_FILE:
        print(f"警告: 公司池文件不存在 {path}，回退默认池")
    return DEFAULT_POOL_FILE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="金融分析与投资决策 Agent")
    sub = parser.add_subparsers(dest="task", required=True)

    # L3 修复：--output-dir 同时挂到各子命令（放子命令后不再报
    # unrecognized arguments）；两层均用 SUPPRESS 默认值，避免子
    # parser 默认值覆盖主 parser 已解析的显式传参（旧用法兼容）
    def _with_output_dir(p):
        p.add_argument(
            "--output-dir", default=argparse.SUPPRESS, help="输出目录")
        return p

    p_company = _with_output_dir(sub.add_parser("company", help="生成公司研报"))
    p_company.add_argument("--target", required=True, help="股票代码（须在公司池列表内）")
    p_company.add_argument("--name", default="", help="公司名称")
    p_company.add_argument("--period", default="", help="报告周期")
    p_company.add_argument("--save", action="store_true", help="保存到输出目录")

    p_industry = _with_output_dir(sub.add_parser("industry", help="生成行业研报"))
    p_industry.add_argument("--name", required=True, help="行业/板块名称（须为公司池板块名，如 消费板块）")
    p_industry.add_argument(
        "--pool-file", default="",
        help="公司池列表 xlsx 路径（缺省 example/上市公司列表.xlsx）")
    p_industry.add_argument("--save", action="store_true")

    p_macro = _with_output_dir(sub.add_parser("macro", help="生成宏观研报"))
    p_macro.add_argument("--period", required=True, help="报告周期，如 2026Q2")
    p_macro.add_argument(
        "--pool-file", default="",
        help="公司池列表 xlsx 路径（缺省 example/上市公司列表.xlsx）")
    p_macro.add_argument("--save", action="store_true")

    p_invest = _with_output_dir(sub.add_parser("invest", help="投资决策：选股 + 仓位配置"))
    p_invest.add_argument(
        "--pool-file", required=True,
        help="公司池列表 xlsx 路径（example/上市公司列表.xlsx）",
    )
    p_invest.add_argument(
        "--sector", default="",
        help="板块名（单板块批量打通，如 消费）；缺省全池")
    p_invest.add_argument("--save", action="store_true")
    p_invest.add_argument(
        "--skip-reports", action="store_true",
        help="只做评分与仓位配置，不为入选标的生成研报"
             "（Swarmflow 决策阶段用，报告由报告阶段单独驱动）")
    p_invest.add_argument(
        "--use-cached-scores", action="store_true",
        help="复用分析阶段评分缓存 decision_log/scores_cache.json"
             "（阶段间状态传递，缺失时回退实时采集分析）")
    p_invest.add_argument(
        "--max-positions", type=int, default=0,
        help="最大持仓标的数（达标标的过多时按评分取前 N，"
             "依据回写决策日志；0=不限制）")

    p_pool = sub.add_parser("pool", help="校验公司池并枚举板块/标的清单")
    p_pool.add_argument(
        "--pool-file", default=argparse.SUPPRESS,
        help="公司池列表 xlsx 路径（缺省 example/上市公司列表.xlsx）")

    p_research = _with_output_dir(sub.add_parser(
        "research", help="公司池采集/分析两阶段（Swarmflow 工作流用）"))
    p_research.add_argument(
        "--pool-file", default=argparse.SUPPRESS,
        help="公司池列表 xlsx 路径（缺省 example/上市公司列表.xlsx）")
    p_research.add_argument(
        "--stage", required=True, choices=["collect", "analyze"],
        help="collect=仅采集落盘（缓存优先）；analyze=读缓存分析并因子打分")
    p_research.add_argument("--sector", default="", help="板块名；缺省全池")
    p_research.add_argument(
        "--save", action="store_true",
        help="analyze 阶段落盘评分缓存 scores_cache.json")

    parser.add_argument(
        "--output-dir", default=argparse.SUPPRESS, help="输出目录"
    )
    # 方案 1：新闻三阶段质量过滤开关（默认关闭，复现保持旧口径；
    # 主 parser 参数须置于子命令之前）
    parser.add_argument(
        "--news-filter", action="store_true",
        help="启用新闻三阶段质量过滤（Stage 0 权威直通 + Stage 1 规则粗筛）")
    parser.add_argument(
        "--news-filter-llm", action="store_true",
        help="新闻过滤追加 Stage 2 LLM 相关性精评"
             "（须与 --news-filter 同用，消耗 LLM token）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # 两层均未显式传 --output-dir 时用默认目录（SUPPRESS 不写 namespace）；
    # 缺省锚定项目根，避免技能根目录执行时产物误落技能内
    output_dir = resolve_output_dir(getattr(args, "output_dir", None))
    config = {"output_dir": output_dir}
    max_positions = getattr(args, "max_positions", 0)
    if max_positions:
        config["investor"] = {"max_positions": max_positions}
    # 方案 1：新闻过滤开关注入（LLM 精评默认关，仅显式 --news-filter-llm 开）
    if getattr(args, "news_filter", False):
        config["news_filter"] = {
            "enabled": True,
            "llm_grade_enabled": bool(
                getattr(args, "news_filter_llm", False)),
        }
    orchestrator = ReportOrchestrator(config)

    if args.task == "company":
        request = ReportRequest(
            report_type="company", target=args.target,
            name=args.name, period=args.period,
        )
        result = orchestrator.generate(request)
        print(f"审查{'通过' if result.passed_review else '未通过'}: {result.review_notes}")
        if args.save and result.content:
            path = orchestrator.save_report(result, f"{args.target}.md")
            print(f"研报已保存: {path}")
            if result.portfolio:
                print(f"投资建议: {json.dumps(result.portfolio, ensure_ascii=False)}")

    elif args.task == "industry":
        # Day 6：自定义池透传 config，Planner 注入板块成分（共享 dict 引用）
        if args.pool_file:
            config["pool_file"] = resolve_pool_file(args.pool_file)
        request = ReportRequest(report_type="industry", target=args.name, name=args.name)
        result = orchestrator.generate(request)
        print(f"审查{'通过' if result.passed_review else '未通过'}: {result.review_notes}")
        if args.save and result.content:
            # 板块名含 "/"（如 科技/AI/半导体板块）时清洗为合法文件名
            safe_name = args.name.replace("/", "_")
            path = orchestrator.save_report(result, f"industry_{safe_name}.md")
            print(f"行业研报已保存: {path}")

    elif args.task == "macro":
        if args.pool_file:
            config["pool_file"] = resolve_pool_file(args.pool_file)
        request = ReportRequest(
            report_type="macro", target=args.period, period=args.period
        )
        result = orchestrator.generate(request)
        print(f"审查{'通过' if result.passed_review else '未通过'}: {result.review_notes}")
        if args.save and result.content:
            path = orchestrator.save_report(result, f"macro_{args.period}.md")
            print(f"宏观研报已保存: {path}")

    elif args.task == "invest":
        pool_file = resolve_pool_file(args.pool_file)
        # M2 修复：自定义池透传 config，Planner 与 Investor 同口径
        config["pool_file"] = pool_file
        scores = None
        if getattr(args, "use_cached_scores", False):
            cache_path = os.path.join(
                output_dir, "decision_log", "scores_cache.json")
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, encoding="utf-8") as f:
                        scores = json.load(f).get("scores")
                    print(f"复用分析阶段评分缓存: {cache_path}")
                except (OSError, json.JSONDecodeError) as e:
                    print(f"评分缓存读取失败，回退实时采集分析: {e}")
            else:
                print("未找到评分缓存，回退实时采集分析")
        portfolio = orchestrator.run_investment(
            pool_file, save=args.save, sector=args.sector,
            reports=not args.skip_reports, scores=scores)
        print(json.dumps(portfolio, ensure_ascii=False, indent=2))

    elif args.task == "pool":
        pool_file = resolve_pool_file(
            getattr(args, "pool_file", DEFAULT_POOL_FILE))
        from collectors.pool_loader import load_pool
        pool = load_pool(pool_file)
        print(json.dumps({
            "pool_file": pool_file,
            "sectors": list(pool.keys()),
            "symbols": {s: [c for c, _ in items]
                        for s, items in pool.items()},
            "total": sum(len(items) for items in pool.values()),
        }, ensure_ascii=False, indent=2))

    elif args.task == "research":
        pool_file = resolve_pool_file(
            getattr(args, "pool_file", DEFAULT_POOL_FILE))
        # M2 修复：自定义池透传 config，与 Investor 同口径
        config["pool_file"] = pool_file
        if args.stage == "collect":
            result = orchestrator.collect_pool(pool_file, args.sector)
        else:
            result = orchestrator.score_pool(
                pool_file, args.sector, save=args.save)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # Day 5 资源消耗记录：--save 运行时落盘 run_stats.json
    # （各阶段耗时 + LLM token 消耗 + 失败留痕 + 随机种子）
    if getattr(args, "save", False):
        stats_path = RUN_STATS.save(output_dir)
        print(f"运行统计已保存: {stats_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
