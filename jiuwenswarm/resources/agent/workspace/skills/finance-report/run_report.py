# -*- coding: utf-8 -*-
"""命令行入口脚本

支持四类任务：
- company  : 生成公司研报（输出 {股票代码}.md）
- industry : 生成行业研报
- macro    : 生成宏观研报
- invest   : 投资决策（公司池选股 + 仓位配置，输出 Portfolio.json）

用法示例：
    python run_report.py company --target 600519 --name 贵州茅台 --save
    python run_report.py industry --name 半导体 --save
    python run_report.py macro --period 2026Q2 --save
    python run_report.py invest --pool-file example/上市公司列表.xlsx --save
"""

import argparse
import json
import os
import sys

from orchestrator import ReportOrchestrator, ReportRequest

# 默认输出目录（提交格式对齐：个股投资研报/股票代码.md + Portfolio.json）
DEFAULT_OUTPUT_DIR = os.path.join("reports", "finance-report")


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
    p_industry.add_argument("--name", required=True, help="行业/板块名称")
    p_industry.add_argument("--save", action="store_true")

    p_macro = _with_output_dir(sub.add_parser("macro", help="生成宏观研报"))
    p_macro.add_argument("--period", required=True, help="报告周期，如 2026Q2")
    p_macro.add_argument("--save", action="store_true")

    p_invest = _with_output_dir(sub.add_parser("invest", help="投资决策：选股 + 仓位配置"))
    p_invest.add_argument(
        "--pool-file", required=True,
        help="公司池列表 xlsx 路径（example/上市公司列表.xlsx）",
    )
    p_invest.add_argument("--save", action="store_true")

    parser.add_argument(
        "--output-dir", default=argparse.SUPPRESS, help="输出目录"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # 两层均未显式传 --output-dir 时才用默认目录（SUPPRESS 不写 namespace）
    output_dir = getattr(args, "output_dir", DEFAULT_OUTPUT_DIR)
    orchestrator = ReportOrchestrator({"output_dir": output_dir})

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
        request = ReportRequest(report_type="industry", target=args.name, name=args.name)
        result = orchestrator.generate(request)
        if args.save and result.content:
            path = orchestrator.save_report(result, f"industry_{args.name}.md")
            print(f"行业研报已保存: {path}")

    elif args.task == "macro":
        request = ReportRequest(
            report_type="macro", target=args.period, period=args.period
        )
        result = orchestrator.generate(request)
        if args.save and result.content:
            path = orchestrator.save_report(result, f"macro_{args.period}.md")
            print(f"宏观研报已保存: {path}")

    elif args.task == "invest":
        portfolio = orchestrator.run_investment(args.pool_file, save=args.save)
        print(json.dumps(portfolio, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
