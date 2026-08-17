# -*- coding: utf-8 -*-
"""CodeExecutor 单元测试：AST 白名单拦截、持久化状态、
输出捕获与错误处理（IPython 真实执行，无网络请求）"""

import pytest

from analyzers.code_executor import CodeExecutor

pytest.importorskip("IPython")
pytest.importorskip("pandas")


@pytest.fixture
def executor(tmp_path):
    return CodeExecutor(chart_dir=str(tmp_path / "charts"))


class TestSafetyWhitelist:
    def test_blocks_import_os(self, executor):
        ok, msg = executor.execute("import os")
        assert not ok and "白名单" in msg and "os" in msg

    def test_blocks_import_subprocess_and_sys(self, executor):
        for code in ("import subprocess", "from sys import path"):
            ok, msg = executor.execute(code)
            assert not ok, code

    def test_blocks_eval_exec_compile(self, executor):
        for code in ("eval('1+1')", "exec('x=1')", "compile('1','','eval')"):
            ok, msg = executor.execute(code)
            assert not ok, code

    def test_blocks_dunder_escape(self, executor):
        ok, msg = executor.execute("().__class__.__subclasses__()")
        assert not ok and "__subclasses__" in msg

    def test_syntax_error_rejected(self, executor):
        ok, msg = executor.execute("def broken(:")
        assert not ok and "语法错误" in msg

    def test_allows_safe_imports(self, executor):
        ok, _ = executor.execute("import math\nimport datetime")
        assert ok

    def test_check_safety_pass_reason(self, executor):
        ok, reason = executor.check_safety("import os")
        assert not ok and "禁止导入模块 'os'" == reason


class TestStatefulExecution:
    def test_variables_persist_across_cells(self, executor):
        ok1, _ = executor.execute("x = 21 * 2")
        ok2, out2 = executor.execute("print(x + 1)")
        assert ok1 and ok2
        assert "43" in out2

    def test_stdout_captured(self, executor):
        ok, out = executor.execute("print('ROE 计算完成')")
        assert ok and "ROE 计算完成" in out

    def test_expression_result_returned(self, executor):
        ok, out = executor.execute("1 + 2")
        assert ok and "3" in out

    def test_runtime_error_reported(self, executor):
        ok, out = executor.execute("1 / 0")
        assert not ok and "ZeroDivisionError" in out

    def test_pandas_roe_computation(self, executor):
        """验收场景：CodeExecutor 内跑通 ROE 计算"""
        ok1, _ = executor.execute(
            "import pandas as pd\n"
            "df = pd.DataFrame({'净利润': [413.0, 466.0], "
            "'股东权益': [1900.0, 2050.0]})")
        assert ok1
        ok2, out2 = executor.execute(
            "roe = df['净利润'].iloc[-1] / df['股东权益'].iloc[-1] * 100\n"
            "print(f'ROE={roe:.1f}%')")
        assert ok2 and "ROE=22.7%" in out2

    def test_new_dataframe_var_compressed(self, executor):
        ok, out = executor.execute(
            "import pandas as pd\n"
            "big = pd.DataFrame({'a': range(100)})")
        assert ok and "big" in out and "DataFrame" in out

    def test_cell_count_and_reset(self, executor):
        executor.execute("y = 1")
        assert executor.cell_count == 1
        executor.reset()
        assert executor.cell_count == 0
        ok, out = executor.execute("print(y)")  # 变量已清除
        assert not ok and "NameError" in out

    def test_matplotlib_preimported(self, executor):
        """验收场景：画图链路可用（Agg 后端无需显示）"""
        ok, out = executor.execute(
            "fig = plt.figure()\n"
            "plt.plot([1, 2], [3, 4])\n"
            "print('plot ok')")
        assert ok and "plot ok" in out
