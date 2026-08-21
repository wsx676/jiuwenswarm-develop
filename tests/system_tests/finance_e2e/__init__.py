# -*- coding: utf-8 -*-
"""finance-report 端到端评测（赛题 C4「成果可复现」自动化兜底）

设计依据：docs/plans/2026-08-20-finance-report-e2e-design.md（v2）
运行方式：pytest tests/system_tests/finance_e2e/ -m e2e --no-cov -v
（默认 pytest 不触发；需网络与 LLM Key，缺 Key 则 skip）
"""
