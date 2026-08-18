# -*- coding: utf-8 -*-
"""报告撰写 Agent（对接 Day 3 生成层）

职责：
1. 委托 ReportWriter 分治式生成：YAML 大纲 → 逐段撰写
   （每段带前文摘要 + 数据材料，突破单次输出长度限制）
2. 图表由 ChartGenerator 图文同源生成并本地化引用校验
3. 修订回流：接收 Reviewer 反馈（revision_feedback），将问题
   清单映射为章节级修订指令注入重写材料（自检反馈循环的下游）
"""

from typing import Optional


class WriterAgent:
    """报告撰写 Agent"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def write(self, research_data: dict, request,
              revision_feedback: Optional[dict] = None) -> "ReportDraft":
        """按模板分治式生成报告草稿

        Args:
            revision_feedback: Reviewer 上一轮反馈（{"issues": [...],
                "research_data": ...}）；非空时为修订重写轮，
                问题清单映射为章节级修订指令注入材料。
        """
        from generators.report_writer import ReportWriter

        writer = ReportWriter(self.config)
        return writer.write(
            research_data, request, revision_feedback=revision_feedback)
