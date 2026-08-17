# -*- coding: utf-8 -*-
"""CodeExecutor：Notebook 式代码执行器

基于 IPython InteractiveShell 模拟持久化、有状态的执行环境，
让模型生成的分析代码在受控沙箱中执行，变量跨代码块保留：
- 持久化有状态：变量在不同代码块间传递（先取数→算指标→画图）
- AST 静态分析 + 白名单：只允许安全库导入，禁止高风险内置函数
- 预导入常用库、配置中文字体（SimHei），捕获 stdout/stderr
"""

import ast
from typing import List, Tuple


class CodeExecutor:
    """Notebook 式代码执行器"""

    # 白名单：仅允许导入的安全库
    ALLOWED_IMPORTS = {"pandas", "numpy", "matplotlib", "datetime", "math"}
    # 禁止的内置函数
    BLOCKED_BUILTINS = {"exec", "eval", "compile", "__import__"}

    def __init__(self):
        self._shell = None  # IPython InteractiveShell 实例（懒初始化）

    def execute(self, code: str) -> Tuple[bool, str]:
        """执行单个代码块，状态在多次调用间保留

        Returns:
            (是否成功, 输出/错误信息)
        """
        if not self._is_safe(code):
            return False, "代码未通过 AST 白名单校验"
        shell = self._ensure_shell()
        # 在持久化 shell 中执行，捕获 stdout/stderr
        return self._run_in_shell(shell, code)

    def _is_safe(self, code: str) -> bool:
        """AST 静态分析：校验导入白名单与危险调用"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if not all(
                    alias.name.split(".")[0] in self.ALLOWED_IMPORTS
                    for alias in node.names
                ):
                    return False
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Name)
                    and func.id in self.BLOCKED_BUILTINS
                ):
                    return False
        return True

    def _ensure_shell(self):
        """懒初始化 IPython shell，预导入常用库、配置中文字体（SimHei）"""
        # TODO(Day 2): 初始化 InteractiveShellEmbed，
        # 预导入 pandas/numpy/matplotlib 并设置中文字体
        return self._shell

    def _run_in_shell(self, shell, code: str) -> Tuple[bool, str]:
        """在 shell 中执行代码块，返回执行结果与输出"""
        # TODO(Day 2): shell.run_cell(code) 捕获 stdout/stderr，
        # 追踪新生变量并格式化 DataFrame 输出
        return True, ""
