# -*- coding: utf-8 -*-
"""报告撰写 Agent（Day 3/4 实现）

职责：
1. 分治式生成：先生成 YAML 大纲（part_title + part_desc），再逐段撰写
2. 每段传入"前文 + 背景 + 原始材料"，突破单次输出长度限制
3. 严格图片引用规则：只引用真实存在的本地图片
4. 调用 ReportWriter/ChartGenerator 产出结构化 Markdown 草稿
"""

from typing import Optional


class WriterAgent:
    """报告撰写 Agent"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def write(self, research_data: dict, request) -> "ReportDraft":
        """按模板分治式生成报告草稿"""
        # TODO(Day 3/4): 先生成 YAML 大纲，再逐段调用
        # generators.report_writer.ReportWriter 撰写；
        # 图表由 generators.chart_generator.ChartGenerator 生成并本地化
        from generators.report_writer import ReportWriter

        writer = ReportWriter(self.config)
        return writer.write(research_data, request)
