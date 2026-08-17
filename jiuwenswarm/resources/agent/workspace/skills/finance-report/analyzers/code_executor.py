# -*- coding: utf-8 -*-
"""CodeExecutor：Notebook 式代码执行器

基于 IPython InteractiveShell 构建持久化、有状态的执行环境，
让模型生成的分析代码在受控沙箱中执行，变量跨代码块保留：
- 持久化有状态：变量在不同代码块间传递（先取数→算指标→画图）
- AST 静态分析 + 白名单：只允许安全库导入，禁止 exec/eval 等高风险调用
- 预导入 pandas/numpy/matplotlib、配置中文字体（SimHei），
  捕获 stdout/stderr，追踪新变量并格式化 DataFrame 输出
"""

import ast
import io
import logging
import os
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class CodeExecutor:
    """Notebook 式代码执行器"""

    # 白名单：仅允许导入的安全库
    ALLOWED_IMPORTS = {
        "pandas", "numpy", "matplotlib", "datetime", "math",
        "json", "collections", "statistics", "typing", "decimal",
    }
    # 禁止的内置函数调用
    BLOCKED_BUILTINS = {"exec", "eval", "compile", "__import__",
                        "globals", "locals", "vars", "breakpoint",
                        "input", "open", "memoryview"}
    # 禁止访问的 dunder 属性（防运行时逃逸静态检查）
    BLOCKED_ATTRS = {"__subclasses__", "__bases__", "__mro__",
                     "__globals__", "__builtins__", "__code__",
                     "__class__", "__import__"}

    def __init__(self, chart_dir: Optional[str] = None):
        self._shell = None  # IPython InteractiveShell 实例（懒初始化）
        self.cell_count = 0
        # 图表默认输出目录：reports/finance-report/charts
        self.chart_dir = chart_dir or os.path.abspath(os.path.join(
            os.path.dirname(__file__), *[".."] * 7,
            "reports", "finance-report", "charts"))

    # ------------------------------------------------------------------
    # 执行入口
    # ------------------------------------------------------------------
    def execute(self, code: str) -> Tuple[bool, str]:
        """执行单个代码块，状态在多次调用间保留

        Returns:
            (是否成功, 输出/错误信息)
        """
        ok, reason = self.check_safety(code)
        if not ok:
            logger.warning("代码未通过 AST 白名单校验：%s", reason)
            return False, f"代码未通过 AST 白名单校验：{reason}"
        shell = self._ensure_shell()
        if shell is None:
            return False, "IPython 不可用，无法执行代码"
        return self._run_in_shell(shell, code)

    # ------------------------------------------------------------------
    # AST 安全校验
    # ------------------------------------------------------------------
    def check_safety(self, code: str) -> Tuple[bool, str]:
        """AST 静态分析：导入白名单 / 危险调用 / 越权属性"""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"语法错误: {e.msg} (line {e.lineno})"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in self.ALLOWED_IMPORTS:
                        return False, f"禁止导入模块 '{alias.name}'"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root not in self.ALLOWED_IMPORTS:
                    return False, f"禁止从模块 '{node.module}' 导入"
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) \
                        and func.id in self.BLOCKED_BUILTINS:
                    return False, f"禁止调用内置函数 '{func.id}()'"
            elif isinstance(node, ast.Attribute):
                if node.attr in self.BLOCKED_ATTRS:
                    return False, f"禁止访问属性 '{node.attr}'"
        return True, ""

    # ------------------------------------------------------------------
    # IPython 持久化 shell
    # ------------------------------------------------------------------
    def _ensure_shell(self):
        """懒初始化 IPython shell，预导入常用库、配置中文字体（SimHei）"""
        if self._shell is not None:
            return self._shell
        try:
            from IPython.core.interactiveshell import InteractiveShell
        except ImportError:
            logger.warning("IPython 未安装，CodeExecutor 不可用")
            return None
        shell = InteractiveShell.instance()
        shell.colors = "NoColor"
        ns = shell.user_ns
        ns["CHART_DIR"] = self.chart_dir
        os.makedirs(self.chart_dir, exist_ok=True)
        # 预导入常用库（失败不致命，逐库降级）
        try:
            shell.run_cell("import numpy as np\nimport pandas as pd",
                           store_history=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("预导入 numpy/pandas 失败：%s", e)
        try:
            shell.run_cell(
                "import matplotlib\nmatplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\n"
                "plt.rcParams['font.sans-serif'] = ['SimHei', "
                "'Microsoft YaHei', 'DejaVu Sans']\n"
                "plt.rcParams['axes.unicode_minus'] = False",
                store_history=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("预导入 matplotlib/中文字体配置失败：%s", e)
        self._shell = shell
        return shell

    def _run_in_shell(self, shell, code: str) -> Tuple[bool, str]:
        """在持久化 shell 中执行代码块，捕获 stdout/stderr 与异常"""
        self.cell_count += 1
        before = set(shell.user_ns)
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            from contextlib import redirect_stderr, redirect_stdout
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = shell.run_cell(code, store_history=False,
                                        silent=False)
        except Exception as e:  # noqa: BLE001
            return False, f"执行异常：{e}"

        outputs = []
        if stdout.getvalue():
            outputs.append(stdout.getvalue().rstrip())
        if stderr.getvalue():
            outputs.append(f"[stderr]\n{stderr.getvalue().rstrip()}")

        if result.error_in_exec is not None or result.error_before_exec:
            err = result.error_in_exec or result.error_before_exec
            outputs.append(f"[错误] {type(err).__name__}: {err}")
            return False, "\n".join(outputs)

        # 表达式结果（最后一个表达式的值）
        if result.result is not None:
            outputs.append(self._format_value(result.result))
        # 新变量摘要（DataFrame 按"表头+前后5行"压缩，防上下文膨胀）
        new_vars = self._describe_new_vars(shell.user_ns, before)
        if new_vars:
            outputs.append(new_vars)
        return True, "\n".join(outputs)

    @staticmethod
    def _format_value(value) -> str:
        try:
            import pandas as pd
            if isinstance(value, pd.DataFrame):
                return CodeExecutor._compress_frame(value)
        except ImportError:
            pass
        text = repr(value)
        return text[:2000] + ("…（截断）" if len(text) > 2000 else "")

    @staticmethod
    def _compress_frame(df, head: int = 5, tail: int = 5) -> str:
        """DataFrame 压缩预览：表头 + 前 5 行 + 后 5 行"""
        if len(df) <= head + tail:
            return df.to_string()
        return (f"{df.head(head).to_string()}\n"
                f"…（中间省略 {len(df) - head - tail} 行，"
                f"共 {len(df)} 行）…\n"
                f"{df.tail(tail).to_string()}")

    def _describe_new_vars(self, ns: dict, before: set) -> str:
        """追踪本代码块新生成的变量（跳过模块与下划线开头）"""
        import types
        lines = []
        for name in sorted(set(ns) - before):
            if name.startswith("_"):
                continue
            val = ns[name]
            if isinstance(val, types.ModuleType):
                continue
            try:
                import pandas as pd
                if isinstance(val, pd.DataFrame):
                    lines.append(f"[新变量] {name}: DataFrame"
                                 f"({val.shape[0]} 行 × {val.shape[1]} 列)")
                    continue
            except ImportError:
                pass
            brief = repr(val)
            lines.append(f"[新变量] {name}: "
                         f"{brief[:120]}{'…' if len(brief) > 120 else ''}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def reset(self):
        """重置执行状态（保留预导入），用于切换标的"""
        if self._shell is not None:
            self._shell.reset(new_session=False)
            self._shell.run_cell(
                "import numpy as np\nimport pandas as pd",
                store_history=False)
        self.cell_count = 0
