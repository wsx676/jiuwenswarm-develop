"""回归测试：《JiuwenSwarm-全项目深度评审》修复项（H1/M2/M3/M4/L4）。

说明：team_helpers/skill_manager 依赖 openjiuwen（git 依赖，仅 CI
环境安装），本机无该依赖时相关用例自动 skip，不影响收集。
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_GATE_PATH = (
    _REPO_ROOT
    / "jiuwenswarm/resources/agent/workspace/skills/skill-omni-creation/scripts/environment_gate.py"
)


def _load_env_gate():
    spec = importlib.util.spec_from_file_location("environment_gate_under_test", _ENV_GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_runtime_modules():
    """按文件加载 team_helpers/skill_manager，缺 openjiuwen 时返回 None。"""
    base = _REPO_ROOT / "jiuwenswarm/server/runtime"
    try:
        th_spec = importlib.util.spec_from_file_location(
            "team_helpers_under_test", base / "agent_adapter/team_helpers.py"
        )
        th_mod = importlib.util.module_from_spec(th_spec)
        th_spec.loader.exec_module(th_mod)
        sm_spec = importlib.util.spec_from_file_location(
            "skill_manager_under_test", base / "skill/skill_manager.py"
        )
        sm_mod = importlib.util.module_from_spec(sm_spec)
        sm_spec.loader.exec_module(sm_mod)
        return th_mod, sm_mod
    except ModuleNotFoundError:
        return None, None


_TEAM_HELPERS, _SKILL_MANAGER = _load_runtime_modules()
_needs_runtime = pytest.mark.skipif(
    _TEAM_HELPERS is None,
    reason="openjiuwen 未安装（仅 CI 环境具备），跳过框架层用例",
)


class _FakeGitProc:
    def __init__(self, delay: float, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.delay = delay
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(self.delay)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


# ---------------------------------------------------------------------------
# H1: git 子进程超时保护 + marketplace 并发同步
# ---------------------------------------------------------------------------


@_needs_runtime
def test_git_subprocess_timeout_kills_and_returns_none(monkeypatch):
    sm = _SKILL_MANAGER
    fake = _FakeGitProc(delay=1.0)

    async def _fake_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr(sm.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(sm, "_GIT_SUBPROCESS_TIMEOUT", 0.01)
    mgr = sm.SkillManager.__new__(sm.SkillManager)
    result = asyncio.run(mgr._run_git_subprocess(["--version"], "version"))
    assert result is None
    assert fake.killed


@_needs_runtime
def test_git_subprocess_success_returns_tuple(monkeypatch):
    sm = _SKILL_MANAGER
    fake = _FakeGitProc(delay=0.0, stdout=b"git version 2.40\n", returncode=0)

    async def _fake_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr(sm.asyncio, "create_subprocess_exec", _fake_exec)
    mgr = sm.SkillManager.__new__(sm.SkillManager)
    result = asyncio.run(mgr._run_git_subprocess(["--version"], "version"))
    assert result == (0, "git version 2.40\n", "")


@_needs_runtime
def test_sync_marketplace_parallel_isolated_failure(tmp_path, monkeypatch):
    """单个 marketplace 失败不阻塞其他（H1：并发 + 隔离失败）。"""
    sm = _SKILL_MANAGER
    mgr = sm.SkillManager(workspace_dir=str(tmp_path / "ws"))
    monkeypatch.setattr(
        mgr,
        "_get_marketplaces",
        lambda: [
            {"name": "repo-a", "url": "http://a", "enabled": True},
            {"name": "repo-b", "url": "http://b", "enabled": True},
        ],
    )
    seen: list[str] = []

    async def _pull(path):
        seen.append(path.name)
        if path.name == "repo-a":
            raise RuntimeError("boom")

    async def _clone(url, dest):
        seen.append(dest.name)

    monkeypatch.setattr(mgr, "_git_pull", _pull)
    monkeypatch.setattr(mgr, "_git_clone", _clone)
    (mgr._marketplace_dir / "repo-a").mkdir(parents=True)  # 已存在 → pull
    asyncio.run(mgr._sync_marketplace_repos())
    assert set(seen) == {"repo-a", "repo-b"}


# ---------------------------------------------------------------------------
# M2: 安装 job 表容量上限 + 终态优先驱逐
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_install_jobs():
    sm = _SKILL_MANAGER
    if sm is None:
        yield
        return
    saved = dict(sm._SKILLNET_INSTALL_JOBS)
    sm._SKILLNET_INSTALL_JOBS.clear()
    yield
    sm._SKILLNET_INSTALL_JOBS.clear()
    sm._SKILLNET_INSTALL_JOBS.update(saved)


@_needs_runtime
def test_install_jobs_evicts_terminal_first(clean_install_jobs, monkeypatch):
    sm = _SKILL_MANAGER
    monkeypatch.setattr(sm, "_MAX_INSTALL_JOBS", 3)
    mgr = sm.SkillManager.__new__(sm.SkillManager)
    mgr._set_install_job("a", {"status": "pending"})
    mgr._set_install_job("b", {"status": "done"})
    mgr._set_install_job("c", {"status": "failed"})
    mgr._set_install_job("d", {"status": "pending"})
    # 超限时先驱逐终态旧记录（b=done），保留 a/c/d
    assert set(sm._SKILLNET_INSTALL_JOBS) == {"a", "c", "d"}


@_needs_runtime
def test_install_jobs_same_id_update_no_growth(clean_install_jobs, monkeypatch):
    sm = _SKILL_MANAGER
    monkeypatch.setattr(sm, "_MAX_INSTALL_JOBS", 100)
    mgr = sm.SkillManager.__new__(sm.SkillManager)
    mgr._set_install_job("a", {"status": "pending"})
    mgr._set_install_job("a", {"status": "failed", "detail": "x"})
    assert len(sm._SKILLNET_INSTALL_JOBS) == 1
    assert sm._SKILLNET_INSTALL_JOBS["a"]["status"] == "failed"


# ---------------------------------------------------------------------------
# M3: _rmtree_or_fail 失败快速失败并留痕
# ---------------------------------------------------------------------------


@_needs_runtime
def test_rmtree_or_fail_success_removes_dir(tmp_path):
    target = tmp_path / "old"
    target.mkdir()
    assert _SKILL_MANAGER._rmtree_or_fail(target, "测试") is None
    assert not target.exists()


@_needs_runtime
def test_rmtree_or_fail_returns_failure_dict(tmp_path, monkeypatch):
    sm = _SKILL_MANAGER
    monkeypatch.setattr(sm, "_safe_rmtree", lambda path: False)
    err = sm._rmtree_or_fail(tmp_path / "locked", "技能强装")
    assert err is not None
    assert err["success"] is False
    assert err["detail_key"] == "skills.common.errors.removeFailed"
    assert "locked" in err["detail"]


# ---------------------------------------------------------------------------
# M4: 截断头尾保留（尾部异常堆栈不丢）
# ---------------------------------------------------------------------------


@_needs_runtime
def test_truncate_text_keeps_tail_stack():
    th = _TEAM_HELPERS
    text = "A" * 400 + "\nTraceback (most recent call last):\n" + "B" * 200 + "FAILED"
    out = th._truncate_team_tool_result_text(text, 512)
    assert out.startswith("A" * 20)
    assert out.endswith("FAILED")
    assert "truncated" in out


@_needs_runtime
def test_truncate_text_short_input_unchanged_via_event():
    ev = {"event_type": "chat.tool_result", "result": "short"}
    out = _TEAM_HELPERS._truncate_team_tool_result_event(ev)
    assert out["result"] == "short"
    assert "truncated" not in out


@_needs_runtime
def test_truncate_event_keeps_tail_and_marks():
    ev = {"event_type": "chat.tool_result", "result": "x" * 1000}
    out = _TEAM_HELPERS._truncate_team_tool_result_event(ev)
    assert out["truncated"] is True
    assert out["original_size"] == 1000
    assert out["result"].endswith("x" * 153)  # 尾部 30% 保留


# ---------------------------------------------------------------------------
# L4: sudo 自动提权默认禁用，需显式 opt-in
# ---------------------------------------------------------------------------


def test_sudo_default_denied_with_hint(monkeypatch):
    gate = _load_env_gate()
    monkeypatch.delenv("OMNI_GATE_ALLOW_SUDO", raising=False)
    monkeypatch.setattr(gate.sys.stdin, "isatty", lambda: False)
    ok, reason = gate._sudo_auto_allowed()
    assert ok is False
    assert "OMNI_GATE_ALLOW_SUDO" in reason


def test_sudo_env_opt_in_allowed(monkeypatch):
    gate = _load_env_gate()
    monkeypatch.setenv("OMNI_GATE_ALLOW_SUDO", "1")
    ok, reason = gate._sudo_auto_allowed()
    assert ok is True
    assert reason == ""


def test_install_linux_deps_reports_skip_reason(monkeypatch):
    gate = _load_env_gate()
    monkeypatch.setattr(gate, "_linux_distribution", lambda: ("ubuntu", ""))
    monkeypatch.setattr(gate, "_has_noninteractive_admin", lambda: True)
    monkeypatch.setattr(gate.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.delenv("OMNI_GATE_ALLOW_SUDO", raising=False)
    monkeypatch.setattr(gate.sys.stdin, "isatty", lambda: False)
    ok, reason = gate._install_linux_system_deps()
    assert ok is False
    assert "sudo" in reason
