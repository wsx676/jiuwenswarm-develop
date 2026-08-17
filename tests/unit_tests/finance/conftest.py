# -*- coding: utf-8 -*-
"""finance-report 技能单元测试公共夹具

技能代码位于 jiuwenswarm/resources/agent/workspace/skills/finance-report，
不随主包导入，这里将其加入 sys.path，测试内以 collectors/analyzers 包形式导入。
"""

import sys
from pathlib import Path

SKILL_DIR = (
    Path(__file__).resolve().parents[3]
    / "jiuwenswarm" / "resources" / "agent" / "workspace" / "skills"
    / "finance-report"
)

# 公司池文件（比赛指定 49 家 A 股六大板块）
POOL_FILE = (
    Path(__file__).resolve().parents[3] / "example" / "上市公司列表.xlsx"
)

# 测试模块收集阶段即需要导入 collectors/analyzers 包，顶层注入技能目录
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
